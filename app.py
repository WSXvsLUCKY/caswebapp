from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import random
import logging
import psycopg2
from psycopg2 import OperationalError
import os
from datetime import datetime
import requests
import json # Для парсинга initData (пример)
import urllib.parse # Для парсинга initData (пример)
# import hmac # Для валидации initData (нужно для продакшена!)
# import hashlib # Для валидации initData (нужно для продакшена!)

app = Flask(__name__)
CORS(app)

# Конфигурация
# !! В ПРОДАКШЕНЕ ИСПОЛЬЗУЙТЕ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ДЛЯ КОНФИДЕНЦИАЛЬНЫХ ДАННЫХ !!
# Например: os.environ.get('BOT_TOKEN')
BOT_TOKEN = '7112560650:AAGGs3JMHouw2T5phdfrNZgaDZODxNHrtF0'
POSTGRES_CONFIG = {
    'host': 'dpg-d058p7je5dus73cm4bqg-a.oregon-postgres.render.com',
    'port': 5432,
    'database': 'pr_5zka',
    'user': 'user_admin',
    'password': 'CW6tBkBfYvqWcRVX5E1CIL6m6C2uabDY'
}

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def create_db_connection():
    """Создает соединение с PostgreSQL базой данных"""
    try:
        connection = psycopg2.connect(**POSTGRES_CONFIG)
        return connection
    except OperationalError as e:
        logger.error(f"Error connecting to PostgreSQL: {e}")
        return None

def init_db():
    """Инициализация базы данных и таблиц"""
    connection = create_db_connection()
    if not connection:
        return

    try:
        cursor = connection.cursor()

        # Добавляем колонки current_bet и game_state, если их нет
        try:
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS current_bet DECIMAL(15, 2) DEFAULT 0.00,
                ADD COLUMN IF NOT EXISTS game_state VARCHAR(50) DEFAULT 'idle';
            """)
            logger.info("Added current_bet and game_state columns to users table (if not exists)")
        except OperationalError as e:
             logger.error(f"Error adding columns: {e}") # Логируем, но продолжаем, если колонки уже есть


        # Проверяем существование колонки photo_url
        try:
            cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='users' AND column_name='photo_url'
            """)
            column_exists = cursor.fetchone()

            if not column_exists:
                # Добавляем колонку если она не существует
                cursor.execute("""
                ALTER TABLE users ADD COLUMN photo_url VARCHAR(512)
                """)
                logger.info("Added photo_url column to users table")
        except OperationalError as e:
             logger.error(f"Error adding photo_url column: {e}")


        # Создаем таблицу пользователей (если не существует) - с учетом новых колонок
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            first_name VARCHAR(255),
            balance DECIMAL(15, 2) DEFAULT 1000.00,
            auto_cashout DECIMAL(5, 2) DEFAULT 2.00,
            photo_url VARCHAR(512),
            current_bet DECIMAL(15, 2) DEFAULT 0.00, -- Добавлено
            game_state VARCHAR(50) DEFAULT 'idle', -- Добавлено
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Создаем таблицу истории игр
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            game_type VARCHAR(50),
            bet_amount DECIMAL(15, 2),
            win_amount DECIMAL(15, 2),
            multiplier DECIMAL(10, 2),
            result VARCHAR(10), -- 'win' or 'lose'
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        """)

        # Создаем триггер для обновления updated_at
        cursor.execute("""
        CREATE OR REPLACE FUNCTION update_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DROP TRIGGER IF EXISTS update_users_timestamp ON users;
        CREATE TRIGGER update_users_timestamp
        BEFORE UPDATE ON users
        FOR EACH ROW
        EXECUTE FUNCTION update_timestamp();
        """)

        connection.commit()
        logger.info("Database tables initialized successfully")
    except OperationalError as e:
        logger.error(f"Error initializing database: {e}")
        connection.rollback()
    finally:
        if connection:
            connection.close()

# Инициализация базы данных при старте
init_db()

class User:
    def __init__(self, user_data):
        # Преобразуем user_id к int, если он строка, но сохраняем как есть, если не числовой (хотя TG ID всегда число)
        self.user_id = int(user_data['id']) if isinstance(user_data['id'], (str, float)) and str(user_data['id']).isdigit() else user_data['id']
        self.username = user_data.get('username', '')
        self.first_name = user_data.get('first_name', 'User')
        self.photo_id = user_data.get('photo_id', '')

        # Данные, которые будут загружены из БД
        self.balance = 1000.00
        self.auto_cashout = 2.00
        self.current_bet = 0.00 # Теперь загружается из БД
        self.game_state = 'idle' # Теперь загружается из БД
        self.photo_url = ''

        self.load_from_db()


    def load_from_db(self):
        """Загружает данные пользователя (включая current_bet и game_state) из базы данных"""
        connection = create_db_connection()
        if not connection:
            return

        try:
            cursor = connection.cursor()
            # Загружаем новые колонки current_bet и game_state
            cursor.execute("""
            SELECT username, first_name, balance, auto_cashout, photo_url, current_bet, game_state
            FROM users WHERE user_id = %s
            """, (self.user_id,))
            user = cursor.fetchone()

            if not user:
                logger.info(f"User {self.user_id} not found in DB, creating new.")
                # Создаем нового пользователя
                if self.photo_id:
                    self.photo_url = self.get_telegram_photo_url()

                cursor.execute("""
                INSERT INTO users (user_id, username, first_name, balance, auto_cashout, photo_url, current_bet, game_state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.user_id, self.username, self.first_name, self.balance, self.auto_cashout, self.photo_url, self.current_bet, self.game_state))
                connection.commit()
            else:
                logger.info(f"User {self.user_id} found in DB, loading data.")
                self.username = user[0] or self.username
                self.first_name = user[1] or self.first_name
                self.balance = float(user[2])
                self.auto_cashout = float(user[3])
                self.photo_url = user[4] or ''
                self.current_bet = float(user[5]) # Загружено из БД
                self.game_state = user[6] or 'idle' # Загружено из БД

                # Обновляем фото, если оно изменилось или отсутствует
                if self.photo_id and (not self.photo_url or 'avatar.png' in self.photo_url):
                     logger.info(f"Updating photo for user {self.user_id}")
                     self.photo_url = self.get_telegram_photo_url()
                     if self.photo_url:
                         self.update_photo_url(connection) # Передаем соединение

        except OperationalError as e:
            logger.error(f"Error loading or creating user {self.user_id} from DB: {e}")
            if connection:
                connection.rollback() # Откатываем, если была ошибка при вставке
        finally:
            if connection:
                connection.close()

    def save_game_state(self, connection):
        """Сохраняет состояние игры пользователя (баланс, текущая ставка, состояние игры) в базу данных."""
        if not connection:
             logger.error(f"Cannot save state for user {self.user_id}: No DB connection.")
             raise OperationalError("No database connection") # Вызываем исключение для обработки ошибки выше

        try:
            cursor = connection.cursor()
            cursor.execute("""
                UPDATE users
                SET balance = %s, current_bet = %s, game_state = %s, updated_at = NOW()
                WHERE user_id = %s
            """, (self.balance, self.current_bet, self.game_state, self.user_id))
            # connection.commit() или rollback() должен быть вызван вызывающей функцией
            logger.info(f"Saved state for user {self.user_id}: balance={self.balance}, bet={self.current_bet}, state={self.game_state}")

        except Exception as e:
            logger.error(f"Error saving user game state for user {self.user_id}: {e}")
            raise # Перевызываем исключение, чтобы вызывающая функция могла откатить транзакцию

    def get_telegram_photo_url(self):
        """Получает URL фото профиля из Telegram"""
        # Убедитесь, что photo_id здесь - это file_id фото профиля, а не просто флаг
        if not self.photo_id or self.photo_id == 'None': # Проверка на пустой или строковое 'None'
             return ''

        try:
            # Получаем информацию о файле
            # ! Валидация response.json() здесь важна !
            file_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={self.photo_id}"
            response = requests.get(file_info_url, timeout=5) # Добавлен таймаут
            response.raise_for_status() # Вызовет исключение для кодов ошибок HTTP
            result = response.json()

            if result.get('ok') and result.get('result'):
                 file_path = result['result'].get('file_path')
                 if file_path:
                     photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                     logger.info(f"Got Telegram photo URL for {self.user_id}: {photo_url}")
                     return photo_url
                 else:
                     logger.warning(f"file_path not found in Telegram getFile response for {self.user_id}")
            else:
                 logger.warning(f"Telegram getFile response not OK for {self.user_id}: {result.get('description', 'No description')}")

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error getting Telegram photo URL for {self.user_id}: {e}")
        except Exception as e:
            logger.error(f"Error processing Telegram photo URL for {self.user_id}: {e}")

        return ''

    def update_photo_url(self, connection):
        """Обновляет URL фото в базе данных"""
        if not self.photo_url or not connection:
            return

        try:
            cursor = connection.cursor()
            cursor.execute("""
            UPDATE users
            SET photo_url = %s
            WHERE user_id = %s
            """, (self.photo_url, self.user_id))
            # connection.commit() или rollback() должен быть вызван вызывающей функцией
            logger.info(f"Updated photo URL for user {self.user_id} in DB.")

        except Exception as e:
            logger.error(f"Error updating photo URL for user {self.user_id} in DB: {e}")
            raise # Перевызываем исключение

    def update_balance(self, amount, connection):
        """Обновляет баланс пользователя в базе данных. Использовать save_game_state предпочтительнее для транзакций."""
        # Этот метод оставлен для примера, но для атомарности лучше использовать save_game_state
        if not connection:
             logger.error(f"Cannot update balance for user {self.user_id}: No DB connection.")
             return False

        try:
            cursor = connection.cursor()
            cursor.execute("""
            UPDATE users
            SET balance = balance + %s
            WHERE user_id = %s
            RETURNING balance
            """, (amount, self.user_id))

            new_balance = cursor.fetchone() # Получаем результат

            if new_balance:
                 self.balance = float(new_balance[0])
                 # connection.commit() или rollback() должен быть вызван вызывающей функцией
                 logger.info(f"Updated balance for user {self.user_id}. New balance: {self.balance}")
                 return True
            else:
                 logger.error(f"Update balance failed for user {self.user_id}: User not found?")
                 return False

        except Exception as e:
            logger.error(f"Error updating balance for user {self.user_id}: {e}")
            raise # Перевызываем исключение

    def add_to_history(self, game_data, connection):
        """Добавляет запись в историю игр."""
        if not connection:
             logger.error(f"Cannot add history for user {self.user_id}: No DB connection.")
             raise OperationalError("No database connection")

        try:
            cursor = connection.cursor()
            cursor.execute("""
            INSERT INTO game_history
            (user_id, game_type, bet_amount, win_amount, multiplier, result)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                self.user_id,
                game_data['game'],
                game_data.get('bet_amount', 0.0),
                game_data.get('win_amount', 0.0),
                game_data.get('multiplier', 1.0),
                game_data.get('result', 'lose') # По умолчанию 'lose' если не указан win
            ))
            # connection.commit() или rollback() должен быть вызван вызывающей функцией
            logger.info(f"Added history for user {self.user_id}. Game: {game_data['game']}, Result: {game_data.get('result')}")

        except Exception as e:
            logger.error(f"Error adding history for user {self.user_id}: {e}")
            raise # Перевызываем исключение

    def get_history(self, limit=10):
        """Получает историю игр пользователя"""
        connection = create_db_connection()
        if not connection:
            return []

        try:
            cursor = connection.cursor()
            cursor.execute("""
            SELECT game_type, bet_amount, win_amount, multiplier, result
            FROM game_history
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
            """, (self.user_id, limit))

            history = []
            for record in cursor.fetchall():
                history.append({
                    'game': record[0],
                    'bet_amount': float(record[1]),
                    'win_amount': float(record[2]),
                    'multiplier': float(record[3]),
                    'result': record[4]
                })
            logger.info(f"Fetched history for user {self.user_id}. Found {len(history)} records.")
            return history
        except OperationalError as e:
            logger.error(f"Error getting history for user {self.user_id}: {e}")
            return []
        finally:
            if connection:
                connection.close()

# --- Маршруты ---

# Маршрут для главной страницы (меню)
@app.route('/')
def home():
    # В идеале, здесь вы должны получить user_id из валидированных initData Telegram
    # Но в этом примере мы пока полагаемся на user_id из URL параметров,
    # как это было в вашем коде, но это НЕБЕЗОПАСНО и не гарантирует реальный ID
    user_id = request.args.get('user_id', type=int) # Получаем как int
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    # Если user_id не был передан или равен 0, это проблема инициализации Mini App
    if not user_id:
         logger.warning("User ID is missing or 0 in the initial request URL.")
         # Можно вернуть страницу с ошибкой или предложить запустить из Telegram
         user_id = 0 # Продолжаем с 0 для совместимости, но это источник проблемы

    # Создаем временный объект User для загрузки данных из БД (или создания нового)
    # Передаем данные, которые получили (надеясь, что ID реальный)
    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data) # Загружает или создает пользователя в БД

    # Получаем URL фото из объекта пользователя (теперь он загружен из БД или получен от TG)
    photo_url = user.photo_url

    logger.info(f"Rendering home page for user ID: {user.user_id}")
    return render_template('menu.html',
                           user_id=user.user_id, # Передаем user_id из объекта User (загружен из БД или 0)
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=photo_url)

# Маршруты для страниц игр (аналогично home)
@app.route('/aviator')
def aviator():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
         logger.warning("User ID is missing or 0 in the aviator page URL.")
         user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data) # Загружает или создает пользователя в БД

    photo_url = user.photo_url

    logger.info(f"Rendering aviator page for user ID: {user.user_id}")
    return render_template('aviator.html',
                           user_id=user.user_id, # Передаем user_id из объекта User
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=photo_url)

@app.route('/mines')
def mines():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
         logger.warning("User ID is missing or 0 in the mines page URL.")
         user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data) # Загружает или создает пользователя в БД

    photo_url = user.photo_url

    logger.info(f"Rendering mines page for user ID: {user.user_id}")
    return render_template('mines.html',
                           user_id=user.user_id,
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=photo_url)

# API для работы с пользователем (фронтенд запрашивает свои данные)
@app.route('/api/user', methods=['POST'])
def get_user():
    try:
        data = request.get_json()
        # Здесь user_id приходит из userData фронтенда
        user_id_from_frontend = data.get('user', {}).get('id')
        if not user_id_from_frontend:
             logger.error("API /api/user: Missing user ID in request data.")
             return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id_from_frontend}) # Загружаем данные пользователя по ID из запроса
        logger.info(f"API /api/user: Request for user ID: {user.user_id}")

        return jsonify({
            'status': 'success',
            'user': {
                'id': user.user_id,
                'username': user.username,
                'first_name': user.first_name,
                'balance': user.balance,
                'auto_cashout': user.auto_cashout,
                'photo_url': user.photo_url
            }
        })
    except Exception as e:
        logger.error(f"API /api/user error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

# API для получения фото (если нужно получать отдельно)
@app.route('/api/user/photo', methods=['GET'])
def get_user_photo():
    photo_id = request.args.get('photo_id')
    if not photo_id:
        return jsonify({'status': 'error', 'message': 'No photo ID provided'}), 400

    # Можно создать временный объект User или просто вызвать функцию get_telegram_photo_url
    # Для простоты вызовем функцию напрямую
    temp_user = User({'id': 0, 'photo_id': photo_id}) # User ID 0 здесь не важен, нужен только photo_id
    photo_url = temp_user.get_telegram_photo_url()

    if photo_url:
         logger.info(f"API /api/user/photo: Returning photo URL for photo_id: {photo_id}")
         return jsonify({
             'status': 'success',
             'photo_url': photo_url
         })
    else:
         logger.warning(f"API /api/user/photo: Could not get photo URL for photo_id: {photo_id}")
         return jsonify({'status': 'error', 'message': 'Failed to get photo URL from Telegram'}), 500


# API для Авиатора: Инициализация (получение баланса, истории, авто-вывода)
@app.route('/api/aviator/init', methods=['POST'])
def aviator_init():
    try:
        data = request.get_json()
        # Здесь user_id приходит из userData фронтенда
        user_id_from_frontend = data.get('user', {}).get('id')
        if not user_id_from_frontend:
             logger.error("API /api/aviator/init: Missing user ID in request data.")
             return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id_from_frontend}) # Загружаем данные пользователя по ID из запроса
        logger.info(f"API /api/aviator/init: Request for user ID: {user.user_id}")

        history = user.get_history() # Загружаем историю пользователя

        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': history,
            'auto_cashout': user.auto_cashout,
            # Можно вернуть больше данных пользователя, если нужно на фронтенде Авиатора
            'user': {
                'id': user.user_id,
                'first_name': user.first_name,
                'username': user.username,
                'photo_url': user.photo_url
            }
        })
    except Exception as e:
        logger.error(f"API /api/aviator/init error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

# API для Авиатора: Размещение ставки
@app.route('/api/aviator/bet', methods=['POST'])
def aviator_bet():
    connection = create_db_connection()
    if not connection:
        logger.error("API /api/aviator/bet: Database connection error.")
        return jsonify({'status': 'error', 'message': 'Database connection error'}), 500
    connection.autocommit = False # Начинаем транзакцию

    try:
        data = request.get_json()
        # Здесь user_id приходит из userData фронтенда
        user_id_from_frontend = data.get('user', {}).get('id')
        bet_amount = float(data.get('bet_amount', 0))

        if not user_id_from_frontend:
             logger.error("API /api/aviator/bet: Missing user ID in request data.")
             connection.rollback()
             return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id_from_frontend}) # Загружаем данные пользователя по ID из запроса
        logger.info(f"API /api/aviator/bet: Request for user ID: {user.user_id}, bet: {bet_amount}")

        # Проверка состояния игры пользователя
        if user.game_state != 'idle':
            logger.warning(f"API /api/aviator/bet: User {user.user_id} tried to bet while state is {user.game_state}")
            connection.rollback()
            return jsonify({'status': 'error', 'message': f'Game in progress. Current state: {user.game_state}'}), 400

        # Валидация ставки
        if bet_amount < 10:
            logger.warning(f"API /api/aviator/bet: User {user.user_id} tried to bet less than minimum ({bet_amount})")
            connection.rollback()
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10₽'}), 400

        if user.balance < bet_amount:
            logger.warning(f"API /api/aviator/bet: User {user.user_id} not enough balance ({user.balance}) for bet ({bet_amount})")
            connection.rollback()
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400

        # Обновляем состояние пользователя в объекте
        user.balance -= bet_amount # Списываем ставку из баланса в объекте
        user.current_bet = bet_amount # Устанавливаем текущую ставку в объекте
        user.game_state = 'bet_placed' # Устанавливаем состояние игры в объекте

        # Сохраняем обновленное состояние в базу данных в рамках транзакции
        user.save_game_state(connection)

        # Генерируем crash_point (это упрощение, в реальной игре он должен быть один на раунд для всех)
        crash_point = round(random.uniform(1.1, 10.0), 2)
        # В более сложной реализации здесь нужно было бы сохранить crash_point для раунда

        connection.commit() # Подтверждаем транзакцию

        logger.info(f"API /api/aviator/bet: User {user.user_id} placed bet {bet_amount}. New balance: {user.balance}. Crash point: {crash_point}")
        return jsonify({
            'status': 'success',
            'balance': user.balance, # Возвращаем обновленный баланс
            'crash_point': crash_point # В упрощенном варианте, генерируем и отдаем фронтенду
        })

    except Exception as e:
        logger.error(f"API /api/aviator/bet error for user {user_id_from_frontend}: {str(e)}")
        if connection:
             connection.rollback() # Откатываем транзакцию при ошибке
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

# API для Авиатора: Забрать выигрыш (Cashout)
@app.route('/api/aviator/cashout', methods=['POST'])
def aviator_cashout():
    connection = create_db_connection()
    if not connection:
        logger.error("API /api/aviator/cashout: Database connection error.")
        return jsonify({'status': 'error', 'message': 'Database connection error'}), 500
    connection.autocommit = False # Начинаем транзакцию

    try:
        data = request.get_json()
        # Здесь user_id приходит из userData фронтенда
        user_id_from_frontend = data.get('user', {}).get('id')
        multiplier = float(data.get('multiplier', 1.0))
        auto = data.get('auto', False)

        if not user_id_from_frontend:
             logger.error("API /api/aviator/cashout: Missing user ID in request data.")
             connection.rollback()
             return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id_from_frontend}) # Загружаем данные пользователя по ID из запроса
        logger.info(f"API /api/aviator/cashout: Request for user ID: {user.user_id}, multiplier: {multiplier}, auto: {auto}")

        # ** Главная проверка, которая теперь использует состояние из БД **
        if user.game_state != 'bet_placed' or user.current_bet <= 0:
            logger.warning(f"API /api/aviator/cashout: User {user.user_id} has no active bet (state: {user.game_state}, bet: {user.current_bet})")
            connection.rollback()
            return jsonify({'status': 'error', 'message': 'No active bet'}), 400 # Это сообщение вернется фронтенду

        # Расчет выигрыша
        # Используем current_bet, загруженный из базы данных
        win_amount = int(user.current_bet * multiplier) # Округляем до целого рубля

        # Обновляем состояние пользователя в объекте
        user.balance += win_amount # Добавляем выигрыш к балансу в объекте
        user.current_bet = 0.00 # Сбрасываем текущую ставку в объекте
        user.game_state = 'cashed_out' # Устанавливаем состояние игры в объекте

        # Сохраняем обновленное состояние в базу данных в рамках транзакции
        user.save_game_state(connection)

        # Добавляем запись в историю игры в рамках той же транзакции
        history_data = {
            'game': 'aviator',
            'bet_amount': user.current_bet, # Здесь user.current_bet уже 0.00 после сброса
            'win_amount': win_amount,
            'multiplier': multiplier,
            'result': 'win'
        }
        # Чтобы сохранить исходную ставку в истории, нужно сохранить ее перед обнулением current_bet
        original_bet = float(user.current_bet) # Сохраняем перед обнулением
        user.current_bet = 0.00 # Теперь обнуляем
        history_data['bet_amount'] = original_bet # Используем сохраненное значение для истории

        user.add_to_history(history_data, connection)

        connection.commit() # Подтверждаем транзакцию

        logger.info(f"API /api/aviator/cashout: User {user.user_id} cashed out at {multiplier}x. Win: {win_amount}. New balance: {user.balance}")
        return jsonify({
            'status': 'success',
            'balance': user.balance, # Возвращаем обновленный баланс
            'profit': win_amount - original_bet, # Возвращаем прибыль
            'multiplier': multiplier,
            'auto': auto # Возвращаем флаг авто-вывода, если нужен на фронтенде
        })

    except Exception as e:
        user_id_for_log = data.get('user', {}).get('id', 'N/A')
        logger.error(f"API /api/aviator/cashout error for user {user_id_for_log}: {str(e)}")
        if connection:
             connection.rollback() # Откатываем транзакцию при ошибке
        # Если произошла ошибка, но ставка была активной, возможно, нужно попытаться вернуть ставку?
        # Это зависит от желаемой логики обработки ошибок.
        return jsonify({'status': 'error', 'message': 'Internal server error or failed to cash out'}), 500
    finally:
        if connection:
            connection.close()

# API для обработки крушения (серверная логика, которая должна вызываться, когда самолет разбивается)
# ЭТО УПРОЩЕННЫЙ МАРШРУТ! В реальной игре нужна система раундов и таймеров.
@app.route('/api/aviator/crash', methods=['POST'])
def aviator_crash():
     connection = create_db_connection()
     if not connection:
         logger.error("API /api/aviator/crash: Database connection error.")
         return jsonify({'status': 'error', 'message': 'Database connection error'}), 500
     connection.autocommit = False # Начинаем транзакцию

     try:
         data = request.get_json()
         # Здесь user_id приходит из userData фронтенда, но в реальной системе крушение обрабатывается сервером для всех
         user_id_from_frontend = data.get('user', {}).get('id')
         crash_multiplier = float(data.get('multiplier', 1.0)) # Фронтенд может сообщить, где разбилось

         if not user_id_from_frontend:
              logger.error("API /api/aviator/crash: Missing user ID in request data.")
              connection.rollback()
              return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

         user = User({'id': user_id_from_frontend}) # Загружаем данные пользователя
         logger.info(f"API /api/aviator/crash: Handling crash for user ID: {user.user_id} at {crash_multiplier}x")

         # Проверяем, была ли активная ставка и не успел ли пользователь вывести
         if user.game_state == 'bet_placed' and user.current_bet > 0:
              # Пользователь проиграл
              original_bet = float(user.current_bet) # Сохраняем перед обнулением
              user.current_bet = 0.00 # Обнуляем текущую ставку в объекте
              user.game_state = 'crashed' # Устанавливаем состояние игры в объекте

              # Сохраняем обновленное состояние в базу данных
              user.save_game_state(connection)

              # Добавляем запись о проигрыше в историю
              history_data = {
                  'game': 'aviator',
                  'bet_amount': original_bet, # Ставка, которая проиграла
                  'win_amount': 0.00, # Выигрыш 0
                  'multiplier': crash_multiplier, # Множитель, на котором произошло крушение
                  'result': 'lose'
              }
              user.add_to_history(history_data, connection)

              connection.commit() # Подтверждаем транзакцию

              logger.info(f"API /api/aviator/crash: User {user.user_id} lost bet {original_bet} at {crash_multiplier}x")
              return jsonify({
                  'status': 'success',
                  'message': 'Bet lost',
                  'balance': user.balance, # Возвращаем текущий баланс (уже без ставки)
                  'lost_amount': original_bet
              })
         else:
              # Ставки не было, или пользователь уже вывел. Ничего не делаем.
              logger.info(f"API /api/aviator/crash: User {user.user_id} had no active bet or already cashed out.")
              connection.rollback() # Откатываем, так как ничего не меняли
              return jsonify({'status': 'success', 'message': 'No active bet to lose'}), 200 # 200 OK, так как это не ошибка

     except Exception as e:
         user_id_for_log = data.get('user', {}).get('id', 'N/A')
         logger.error(f"API /api/aviator/crash error for user {user_id_for_log}: {str(e)}")
         if connection:
              connection.rollback() # Откатываем транзакцию при ошибке
         return jsonify({'status': 'error', 'message': 'Internal server error during crash handling'}), 500
     finally:
         if connection:
             connection.close()


if __name__ == '__main__':
    # Для локальной разработки
    # В продакшене на Render может потребоваться запуск через Gunicorn или другой WSGI сервер
    logger.info("Starting Flask app...")
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True) # Используем порт из переменной окружения, если доступно
