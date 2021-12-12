from pyrogram import Client

api_id = input("Enter API_ID:\n")
api_hash = input("Enter API_HASH:\n")
bot_token = input("Enter BOT_TOKEN:\n")

client = Client(":memory:", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
client.start()

print("Session String:\n")
print(client.export_session_string())
