import os
import csv 
from groq import Groq
from prompt import build_prompt 


#setting
MODEL = "openai/gpt-oss-20b"

def model_response(prompt,model):
    client = Groq(
        api_key=os.environ.get("GROQ_API_KEY"),
    )
    
    response = client.chat.completions.create(
        messages=[
            {
                "role": "user",
                "content": f"{prompt}",
            }
        ],
        model=f"{model}",
    )
    return response

def evaluate_responses(filename,model):
    with open(filename,newline="") as file:
        next(file)
        reader = csv.reader(file)
        idx = 0
        for row in reader:
            prompt = build_prompt(
            name=row[4],
            instagram=row[3],
            doing=row[7],
            built=row[8],
            bring=row[9],
            share=row[10],
            )
            response = model_response(prompt,model)
            print(row[4])
            print(response.choices[0].message.content)

evaluate_responses("./test.csv",MODEL)
