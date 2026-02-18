import logging
import requests
import os
import asyncio
import uuid
from PIL import Image, ImageDraw
import base64
import json
from typing import List, Dict, Union # Added for type hinting

from agent_network.tools.base_tool import BaseTool

# Configure logging for utility_tools.py
logger = logging.getLogger(__name__)

class PrintTaskTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="print_task",
            description="Prints a message to the console. Takes 'message' as argument."
        )

    def run(self, message: str):
        logger.info(f"[PrintTaskTool]: {message}")
        return {"status": "success", "output": message}

class WebFetchTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="web_fetch",
            description="Fetches content from a URL. Takes 'url' as argument."
        )

    def run(self, url: str):
        logger.info(f"[WebFetchTool]: Attempting to fetch content from {url}")
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
            content = response.text
            logger.info(f"[WebFetchTool]: Successfully fetched content from {url}")
            return {"status": "success", "output": content}
        except requests.exceptions.RequestException as e:
            logger.error(f"[WebFetchTool]: Failed to fetch content from {url}: {e}")
            return {"status": "error", "message": f"Failed to fetch content from {url}: {e}"}

class ImageAnalysisTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="image_analysis",
            description="Analyzes an image file and returns a textual description. Takes 'image_path' as argument."
        )
        self.openrouter_api_key = os.environ.get("OPENROUTER_API_KEY")
        self.openrouter_chat_url = "https://openrouter.ai/api/v1/chat/completions"
        self.multimodal_model = "nvidia/nemotron-nano-12b-v2-vl:free" # Specific free multimodal model

    def run(self, image_path: str):
        logger.info(f"[ImageAnalysisTool]: Analyzing image at {image_path} using {self.multimodal_model}")
        if not os.path.exists(image_path):
            logger.error(f"[ImageAnalysisTool]: Image file not found at {image_path}")
            return {"status": "error", "message": f"Image file not found at {image_path}"}
        
        if not self.openrouter_api_key:
            logger.error("[ImageAnalysisTool]: OPENROUTER_API_KEY not set. Cannot perform image analysis.")
            return {"status": "error", "message": "OPENROUTER_API_KEY not set. Cannot perform image analysis."}

        try:
            # Read image and base64 encode it
            with open(image_path, "rb") as image_file:
                encoded_image = base64.b64encode(image_file.read()).decode("utf-8")

            headers = {
                "Authorization": f"Bearer {self.openrouter_api_key}",
                "Content-Type": "application/json",
            }
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image in detail."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded_image}"}}
                    ],
                }
            ]

            data = {
                "model": self.multimodal_model,
                "messages": messages,
            }

            response = requests.post(self.openrouter_chat_url, headers=headers, data=json.dumps(data))
            response.raise_for_status()
            response_json = response.json()
            
            description = response_json['choices'][0]['message']['content']
            
            logger.info(f"[ImageAnalysisTool]: Analysis complete for {image_path}. Description: {description}")
            return {"status": "success", "output": description}

        except requests.exceptions.RequestException as e:
            logger.error(f"[ImageAnalysisTool]: Error calling OpenRouter API for image analysis: {e}")
            if e.response is not None:
                logger.error(f"OpenRouter API response content: {e.response.text}")
            return {"status": "error", "message": f"Failed to analyze image via OpenRouter: {e}"}
        except Exception as e:
            logger.error(f"[ImageAnalysisTool]: An unexpected error occurred during image analysis: {e}")
            return {"status": "error", "message": f"An unexpected error occurred: {e}"}

class SendImageTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="send_image",
            description="Sends an image file back to the user via the original communication channel. Takes 'image_path' as argument."
        )

    def run(self, image_path: str, context: dict):
        logger.info(f"[SendImageTool]: Attempting to send image from {image_path}")
        if not os.path.exists(image_path):
            logger.error(f"[SendImageTool]: Image file not found at {image_path}")
            return {"status": "error", "message": f"Image file not found at {image_path}"}
        
        connector = context.get("connector")
        if not connector:
            logger.error("[SendImageTool]: No connector found in context to send image.")
            return {"status": "error", "message": "No connector found in context to send image."}
        
        loop = getattr(connector, 'loop', None)
        if loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    connector.send_image(image_path, context),
                    loop
                ).result(timeout=10)
                logger.info(f"[SendImageTool]: Successfully sent image from {image_path}")
                return {"status": "success", "output": f"Image sent from {image_path}"}
            except Exception as e:
                logger.error(f"[SendImageTool]: Failed to send image via connector: {e}")
                return {"status": "error", "message": f"Failed to send image via connector: {e}"}
        else:
            logger.error("[SendImageTool]: Connector does not have a running event loop to send image.")
            return {"status": "error", "message": "Connector does not have a running event loop to send image."}

class ImageGenerationTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="image_generation",
            description="Generates a placeholder image based on a text prompt. Takes 'prompt' as argument."
        )

    def run(self, prompt: str):
        logger.info(f"[ImageGenerationTool]: Generating image for prompt: '{prompt}'")
        try:
            temp_dir = "temp_attachments"
            os.makedirs(temp_dir, exist_ok=True)
            
            filename = f"{temp_dir}/generated_image_{uuid.uuid4()}.png"
            
            img_size = (256, 256)
            img = Image.new('RGB', img_size, color = (73, 109, 137))
            d = ImageDraw.Draw(img)
            d.text((10,10), f"Generated for:\n'{prompt[:30]}...", fill=(255,255,0))
            
            img.save(filename)
            logger.info(f"[ImageGenerationTool]: Placeholder image generated at {filename}")
            return {"status": "success", "output": filename}
        except Exception as e:
            logger.error(f"[ImageGenerationTool]: Failed to generate placeholder image: {e}")
            return {"status": "error", "message": f"Failed to generate placeholder image: {e}"}

class SendUserMessageTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="send_user_message",
            description="Sends a message directly to the user on the dashboard. Takes 'message' as argument, and an optional 'title' for the message."
        )
        self.hub_url = os.environ.get("HUB_URL", "http://127.0.0.1:5000")

    def run(self, message: str, agent_name: str, title: str = None):
        logger.info(f"[SendUserMessageTool]: Agent '{agent_name}' sending message with title '{title}' to user: '{message}'")
        try:
            payload = {"agent_name": agent_name, "message": message, "title": title}
            response = requests.post(f"{self.hub_url}/receive_user_message", json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"[SendUserMessageTool]: Message successfully sent to hub. Response: {response.json()}")
            return {"status": "success", "output": "Message sent to user."}
        except requests.exceptions.RequestException as e:
            logger.error(f"[SendUserMessageTool]: Failed to send message to hub: {e}")
            return {"status": "error", "message": f"Failed to send message to user: {e}"}

class FinishTaskTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="finish_task",
            description="Signals that the current task is complete. Takes 'final_output' as argument, which should be a summary of the task's outcome."
        )

    def run(self, final_output: str):
        logger.info(f"[FinishTaskTool]: Task completed with final output: {final_output}")
        return {"status": "success", "output": final_output, "task_complete": True}

class SendAgentMessageTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="send_agent_message",
            description="Sends a message or delegates a task to another agent in the network. Takes 'agent_name' (the target agent's name) and 'message' (the task or message to send). Use this to collaborate with peer agents on complex tasks."
        )
        self.hub_url = os.environ.get("HUB_URL", "http://127.0.0.1:5000")

    def run(self, agent_name: str, message: str, sender_agent_name: str):
        logger.info(f"[SendAgentMessageTool]: Agent '{sender_agent_name}' sending message to agent '{agent_name}': '{message}'")
        try:
            payload = {"target_agent_name": agent_name, "message": message, "sender_agent_name": sender_agent_name}
            response = requests.post(f"{self.hub_url}/send_message_to_agent_by_name", json=payload, timeout=5)
            response.raise_for_status()
            logger.info(f"[SendAgentMessageTool]: Message successfully sent to hub. Response: {response.json()}")
            return {"status": "success", "output": "Message sent to agent."}
        except requests.exceptions.RequestException as e:
            logger.error(f"[SendAgentMessageTool]: Failed to send message to hub: {e}")
            return {"status": "error", "message": f"Failed to send message to agent: {e}"}

class LLMCallTool(BaseTool):
    def __init__(self, planner_instance): # Accept planner instance
        super().__init__(
            name="llm_call",
            description="Makes a call to the LLM. Takes 'prompt' as argument. Returns the LLM response content."
        )
        self.planner = planner_instance # Store the planner instance

    def run(self, prompt: str) -> Dict[str, Union[str, List[str]]]:
        logger.info(f"[LLMCallTool]: Calling LLM with prompt: '{prompt[:100]}...'")
        try:
            # evaluate_prompt returns {"content": str, "token_usage": dict}
            llm_response = self.planner.evaluate_prompt(prompt)
            return {"status": "success", "output": llm_response["content"]}
        except Exception as e:
            logger.error(f"[LLMCallTool]: Error calling LLM: {e}")
            return {"status": "error", "message": f"Error calling LLM: {e}"}