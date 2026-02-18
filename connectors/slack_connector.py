import threading
import asyncio
import os
import uuid
from agent_network.connectors.base_connector import BaseConnector

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
    from slack_sdk import WebClient
except ImportError:
    raise ImportError(
        "The 'slack_bolt' and 'slack_sdk' libraries are not installed. "
        "Please install them with 'pip install slack_bolt slack_sdk'."
    )


class SlackConnector(BaseConnector):
    def __init__(self, agent, logger, bot_token, app_token, channel_id=None):
        super().__init__(agent, logger)
        if not bot_token or not app_token:
            raise ValueError(
                "Slack bot token (xoxb-...) and app-level token (xapp-...) are required. "
                "Enable Socket Mode in your Slack app settings."
            )
        self.bot_token = bot_token
        self.app_token = app_token
        self.channel_id = channel_id  # Optional: restrict to a specific channel
        self.thread = None
        self.handler = None

        self.slack_app = App(token=self.bot_token)
        self.client = WebClient(token=self.bot_token)

        @self.slack_app.event("message")
        def handle_message_event(event, say):
            self._handle_message(event, say)

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.logger.info("Slack connector started in a background thread.")

    def _run(self):
        try:
            self.handler = SocketModeHandler(self.slack_app, self.app_token)
            self.handler.start()
        except Exception as e:
            self.logger.error(f"An error occurred in the Slack connector thread: {e}")

    def stop(self):
        self.logger.info("Stopping Slack connector...")
        if self.handler:
            try:
                self.handler.close()
            except Exception as e:
                self.logger.error(f"Error stopping Slack handler: {e}")
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.logger.info("Slack connector stopped.")

    def _handle_message(self, event, say):
        # Ignore bot messages
        if event.get("bot_id") or event.get("subtype") == "bot_message":
            return

        channel = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts")
        user_id = event.get("user", "unknown")
        text = event.get("text", "")

        # If channel_id is set, only respond in that channel
        if self.channel_id and channel != self.channel_id:
            return

        self.logger.info(f"Received Slack message from user {user_id} in channel {channel}: {text}")

        # Handle file attachments (images)
        image_path = None
        files = event.get("files", [])
        if files:
            for f in files:
                if f.get("mimetype", "").startswith("image/"):
                    try:
                        temp_dir = "temp_attachments"
                        os.makedirs(temp_dir, exist_ok=True)
                        filename = f"{temp_dir}/{uuid.uuid4()}_{f.get('name', 'image.png')}"
                        # Download the file using the bot token
                        url = f.get("url_private_download") or f.get("url_private")
                        if url:
                            import requests
                            resp = requests.get(url, headers={"Authorization": f"Bearer {self.bot_token}"})
                            if resp.status_code == 200:
                                with open(filename, "wb") as out:
                                    out.write(resp.content)
                                image_path = filename
                                self.logger.info(f"Downloaded Slack image: {image_path}")
                            break
                    except Exception as e:
                        self.logger.error(f"Failed to download Slack file: {e}")

        session_id = f"slack_{channel}_{thread_ts}" if thread_ts else f"slack_{channel}"
        slack_context = {
            "connector": self,
            "channel_id": channel,
            "thread_ts": thread_ts,
            "author": user_id,
            "session_id": session_id,
            "say": say
        }

        self.agent.handle_message(text, slack_context, image_path=image_path)

    async def send_response(self, response_text, context):
        channel = context.get("channel_id")
        thread_ts = context.get("thread_ts")
        if channel:
            try:
                self.client.chat_postMessage(
                    channel=channel,
                    text=response_text,
                    thread_ts=thread_ts
                )
            except Exception as e:
                self.logger.error(f"Failed to send Slack message to channel {channel}: {e}")

    async def send_image(self, image_path, context):
        channel = context.get("channel_id")
        thread_ts = context.get("thread_ts")
        if channel:
            try:
                self.client.files_upload_v2(
                    channel=channel,
                    file=image_path,
                    thread_ts=thread_ts
                )
                self.logger.info(f"Successfully sent image {image_path} to Slack channel {channel}")
            except Exception as e:
                self.logger.error(f"Failed to send image to Slack channel {channel}: {e}")
