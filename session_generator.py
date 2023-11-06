from pyrogram import Client

api_id = input("Enter API_ID:\n'24335028')
api_hash = input("Enter API_HASH:\n'b204ec833fb451fb913fc8e683b232d0')
bot_token = input("Enter BOT_TOKEN:\n '6247730826:AAGICcluDNXCsWOKJuiIRn-ulrBau5I-tmY')

client = Client(":memory:", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
client.start()

print("Session String:\n")
print(client.export_session_string())
