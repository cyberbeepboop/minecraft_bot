from re import S

from cb import LogMessage, MinecraftChatBot
bot = MinecraftChatBot()

bot.whisper_format = "/minecraft:W {player} {message}"
#this defaults to what i just equaled it to here, so you can use it out of the box.

#you need to set latest.log in config.yml for this to work too
#example: C:\Users\U\AppData\Roaming\ModrinthApp\profiles\stinkyfart\logs\latest.log
#should be easy to find

def someone_chatted(message: LogMessage) -> None:
    player = str(message.player)
    if player == "Th4creator":
        bot.send_message(message="Th4creator is lwk the coolest")
    if message.message == "!help":
        bot.send_message(message="You stink pal")
    if message:
        print(f"[{message.group}] {message.player}: {message.message} ({message.kind})")
    if message.message == "whispertome":
        bot.whisper(player=player, message="this is a whisper")
bot.when_message_in_log(callback=someone_chatted)
