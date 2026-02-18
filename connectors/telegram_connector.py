import threading
import asyncio
import os
import uuid
from agent_network.connectors.base_connector import BaseConnector

try:
    from telegram import Update
    from telegram.ext import Application, MessageHandler, ContextTypes, filters
except ImportError:
    raise ImportError("The 'python-telegram-bot' library is not installed. Please install it with 'pip install python-telegram-bot>=20'.")


class TelegramConnector(BaseConnector):
    def __init__(self, agent, logger, token, allowed_chat_ids):
        super().__init__(agent, logger)
        if not token:
            raise ValueError("Telegram token is required for the Telegram connector.")
        self.token = token
        self.allowed_chat_ids = [int(chat_id) for chat_id in allowed_chat_ids] if allowed_chat_ids else []
        self.app = None
        self.loop = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        self.logger.info("Telegram connector started in a background thread.")

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self.app = Application.builder().token(self.token).build()
            # Handle text messages and photos
            self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND | filters.PHOTO, self._handle_message))
            self.loop.run_until_complete(self.app.initialize())
            self.loop.run_until_complete(self.app.start())
            self.loop.run_until_complete(self.app.updater.start_polling())
            self.logger.info("Telegram polling started.")
            self.loop.run_forever()
        except Exception as e:
            self.logger.error(f"An error occurred in the Telegram connector thread: {e}")
        finally:
            if self.app:
                try:
                    self.loop.run_until_complete(self.app.updater.stop())
                    self.loop.run_until_complete(self.app.stop())
                    self.loop.run_until_complete(self.app.shutdown())
                except Exception:
                    pass

    def stop(self):
        self.logger.info("Stopping Telegram connector...")
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
        self.logger.info("Telegram connector stopped.")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        chat_id = update.message.chat_id
        if self.allowed_chat_ids and chat_id not in self.allowed_chat_ids:
            self.logger.warning(f"Received message from unauthorized Telegram chat ID: {chat_id}")
            await update.message.reply_text("You are not authorized to use this bot.")
            return

        message_text = update.message.text if update.message.text else ""
        image_path = None
        message_thread_id = update.message.message_thread_id

        if update.message.photo:
            self.logger.info(f"Received photo from Telegram chat {chat_id}, thread {message_thread_id}")
            try:
                photo = update.message.photo[-1]
                photo_file = await photo.get_file()

                temp_dir = "temp_attachments"
                os.makedirs(temp_dir, exist_ok=True)

                filename = f"{temp_dir}/{uuid.uuid4()}_{photo_file.file_path.split('/')[-1]}"
                await photo_file.download_to_drive(filename)
                image_path = filename
                self.logger.info(f"Downloaded Telegram photo: {image_path}")
            except Exception as e:
                self.logger.error(f"Failed to download Telegram photo: {e}")

        self.logger.info(f"Received message from Telegram user {update.message.from_user.name} in chat {chat_id}, thread {message_thread_id}: {message_text}")

        session_id = f"tg_{chat_id}_{message_thread_id}" if message_thread_id else f"tg_{chat_id}"
        tg_context = {
            "connector": self,
            "chat_id": chat_id,
            "message_thread_id": message_thread_id,
            "author": update.message.from_user.name,
            "session_id": session_id
        }

        self.agent.handle_message(message_text, tg_context, image_path=image_path)

    async def send_response(self, response_text, context):
        chat_id = context.get("chat_id")
        message_thread_id = context.get("message_thread_id")
        if chat_id and self.app:
            try:
                await self.app.bot.send_message(
                    chat_id=chat_id,
                    text=response_text,
                    message_thread_id=message_thread_id
                )
            except Exception as e:
                self.logger.error(f"Failed to send message to Telegram chat {chat_id}: {e}")

    async def send_image(self, image_path, context):
        chat_id = context.get("chat_id")
        message_thread_id = context.get("message_thread_id")
        if chat_id and self.app:
            try:
                with open(image_path, 'rb') as f:
                    await self.app.bot.send_photo(
                        chat_id=chat_id,
                        photo=f,
                        message_thread_id=message_thread_id
                    )
                self.logger.info(f"Successfully sent image {image_path} to Telegram chat {chat_id}")
            except Exception as e:
                self.logger.error(f"Failed to send image {image_path} to Telegram chat {chat_id}: {e}")
