import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

BOT_TOKEN = '7112560650:AAGGs3JMHouw2T5phdfrNZgaDZODxNHrtF0'
WEBAPP_URL = 'https://aviator-2-bhw7.onrender.com'  # Без /aviator в конце!

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="🎮 Играть в Авиатор", 
        web_app=WebAppInfo(url=WEBAPP_URL)  # Открывает корневой путь /
    ))
    
    await message.answer(
        "🛩️ Добро пожаловать в игру Авиатор!\n\n"
        "Самолет взлетает, а множитель растет. "
        "Успейте забрать выигрыш до того, как самолет улетит!",
        reply_markup=builder.as_markup()
    )

@dp.message(Command("game"))
async def cmd_game(message: types.Message):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="🚀 Запустить Авиатор", 
        web_app=WebAppInfo(url=WEBAPP_URL)
    ))
    
    await message.answer(
        "Нажмите кнопку ниже, чтобы начать игру:",
        reply_markup=builder.as_markup()
    )

async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())