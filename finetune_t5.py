import mysql.connector
from transformers import T5Tokenizer, T5ForConditionalGeneration, Trainer, TrainingArguments
import torch
import logging
from datasets import Dataset

# Configure logging
logging.basicConfig(level=logging.INFO, filename='finetune.log', format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Database connection
def get_training_data():
    try:
        conn = mysql.connector.connect(
            host='localhost',
            database='lisa20db',
            user='root',
            password=''  # Update with your MySQL password
        )
        cursor = conn.cursor()
        # Select examples with descriptions and code; prefer higher-rated ones
        cursor.execute("SELECT description, example_code, language FROM learning_data WHERE description IS NOT NULL AND example_code IS NOT NULL AND rating >= 0")
        data = cursor.fetchall()
        conn.close()
        logger.info(f"Retrieved {len(data)} examples from learning_data")
        return data
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        raise

# Prepare data for T5
def prepare_data(data):
    inputs = []
    outputs = []
    for description, example_code, language in data:
        # Format input as a prompt; assume description is the request
        input_text = f"Generate code for: {description} in {language}"
        output_text = example_code.strip()
        inputs.append(input_text)
        outputs.append(output_text)
    logger.info(f"Prepared {len(inputs)} input-output pairs")
    return inputs, outputs

# Custom Dataset class
class CodeDataset(torch.utils.data.Dataset):
    def __init__(self, inputs, outputs, tokenizer, max_length=512):
        self.inputs = tokenizer(inputs, padding="max_length", truncation=True, max_length=max_length, return_tensors="pt")
        self.outputs = tokenizer(outputs, padding="max_length", truncation=True, max_length=max_length, return_tensors="pt")

    def __len__(self):
        return len(self.inputs["input_ids"])

    def __getitem__(self, idx):
        return {
            "input_ids": self.inputs["input_ids"][idx],
            "attention_mask": self.inputs["attention_mask"][idx],
            "labels": self.outputs["input_ids"][idx]  # T5 uses "labels" for target sequences
        }

# Fine-tuning function
def finetune_t5():
    # Load tokenizer and model
    tokenizer = T5Tokenizer.from_pretrained("t5-small")
    model = T5ForConditionalGeneration.from_pretrained("t5-small")

    # Get and prepare data
    data = get_training_data()
    if not data:
        logger.error("No data retrieved from database; exiting")
        return
    inputs, outputs = prepare_data(data)

    # Create dataset
    dataset = CodeDataset(inputs, outputs, tokenizer)
    logger.info(f"Dataset size: {len(dataset)}")

    # Training arguments
    training_args = TrainingArguments(
        output_dir="./t5_finetuned",
        num_train_epochs=3,  # Adjust based on dataset size
        per_device_train_batch_size=4,
        per_device_eval_batch_size=4,
        warmup_steps=500,
        weight_decay=0.01,
        logging_dir='./logs',
        logging_steps=10,
        save_steps=500,
        save_total_limit=2,
        overwrite_output_dir=True,
    )

    # Initialize Trainer
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=dataset,
    )

    # Train the model
    logger.info("Starting fine-tuning")
    trainer.train()
    logger.info("Fine-tuning completed")

    # Save the fine-tuned model
    model.save_pretrained("./t5_finetuned")
    tokenizer.save_pretrained("./t5_finetuned")
    logger.info("Model and tokenizer saved to ./t5_finetuned")

if __name__ == "__main__":
    finetune_t5()