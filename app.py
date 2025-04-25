from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import random
import logging
import psycopg2
from psycopg2 import OperationalError
import os
from datetime import datetime
import requests
import json
import urllib.parse
# import hmac # Для валидации initData (нужно для продакшена!)
# import hashlib # Для валидации initData (нужно для продакшена!)

app = Flask(__name__)
CORS(app) # Разрешаем CORS запросы, чтобы фронтенд мог обращаться к бэкенду

# Конфигурация базы данных и Telegram бота
# !! В ПРОДАКШЕНЕ ИСПОЛЬЗУЙТЕ ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ДЛЯ КОНФИДЕНЦИАЛЬНЫХ ДАННЫХ !!
# Например: os.environ.get('BOT_TOKEN')
BOT_TOKEN = '7112560650:AAGGs3JMHouw2T5phdfrNZgaDZODxNHrtF0' # Замените на ваш токен
POSTGRES_CONFIG = {
    'host': 'dpg-d058p7je5dus73cm4bqg-a.oregon-postgres.render.com', # Замените на ваш хост
    'port': 5432,
    'database': 'pr_5zka', # Замените на имя вашей базы данных
    'user': 'user_admin', # Замените на вашего пользователя
    'password': 'CW6tBkBfYvqWcRVX5E1CIL6m6C2uabDY' # Замените на ваш пароль
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

        # Добавляем колонки current_bet и game_state в таблицу users, если их нет
        # Это нужно для хранения состояния игры пользователя (активная ставка, состояние игры)
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
            balance DECIMAL(15, 2) DEFAULT 1000.00, -- Начальный баланс
            auto_cashout DECIMAL(5, 2) DEFAULT 2.00, -- Множитель для авто-вывода
            photo_url VARCHAR(512), -- URL фото профиля
            current_bet DECIMAL(15, 2) DEFAULT 0.00, -- Текущая активная ставка пользователя
            game_state VARCHAR(50) DEFAULT 'idle', -- Состояние игры пользователя ('idle', 'bet_placed', 'cashed_out', 'crashed')
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Создаем таблицу истории игр
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS game_history (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            game_type VARCHAR(50), -- Тип игры (например, 'aviator', 'mines')
            bet_amount DECIMAL(15, 2), -- Сумма ставки
            win_amount DECIMAL(15, 2), -- Сумма выигрыша (0 при проигрыше)
            multiplier DECIMAL(10, 2), -- Множитель при выводе или крушении
            result VARCHAR(10), -- Результат игры ('win' или 'lose')
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id) -- Связь с таблицей пользователей
        )
        """)

        # Создаем триггер для автоматического обновления колонки updated_at
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

        connection.commit() # Применяем изменения
        logger.info("Database tables initialized successfully")
    except OperationalError as e:
        logger.error(f"Error initializing database: {e}")
        if connection:
            connection.rollback() # Откатываем изменения при ошибке
    finally:
        if connection:
            connection.close()

# Инициализация базы данных при старте приложения
init_db()

# Класс для представления пользователя и взаимодействия с БД
class User:
    def __init__(self, user_data):
        # Преобразуем user_id к int, если он строка, но сохраняем как есть, если не числовой (хотя TG ID всегда число)
        # Добавлена более надежная проверка и обработка случая, если user_data['id'] отсутствует или некорректен
        try:
            user_id_raw = user_data.get('id')
            if user_id_raw is None:
                 raise ValueError("User data missing 'id'")
            self.user_id = int(user_id_raw)
            if self.user_id == 0: # Логируем предупреждение, если ID равен 0
                 logger.warning(f"User object initialized with user_id 0 from data: {user_data}")
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid user ID received: {user_data.get('id')}. Error: {e}")
            # Присваиваем фиктивный ID или генерируем ошибку, в зависимости от логики
            # Для продолжения, можно присвоить 0, но это может вызвать проблемы с БД (primary key)
            # В реальном приложении нужно обработать эту ошибку иначе
            self.user_id = 0 # Присваиваем 0, если ID некорректен, но это может привести к ошибкам БД
            # raise ValueError(f"Invalid user ID: {user_data.get('id')}") # Можно генерировать ошибку


        self.username = user_data.get('username', '')
        self.first_name = user_data.get('first_name', 'User')
        self.photo_id = user_data.get('photo_id', '')

        # Данные, которые будут загружены из БД или получат значения по умолчанию
        self.balance = 1000.00
        self.auto_cashout = 2.00
        self.current_bet = 0.00
        self.game_state = 'idle'
        self.photo_url = ''

        # Автоматически загружаем данные из БД при создании объекта
        self.load_from_db()


    def load_from_db(self):
        """Загружает данные пользователя (включая current_bet и game_state) из базы данных"""
        if self.user_id == 0: # Не пытаемся загрузить или создать пользователя с ID 0
             logger.warning("Attempted to load user with ID 0. Skipping DB interaction.")
             # Устанавливаем значения по умолчанию, если ID 0
             self.balance = 1000.00
             self.auto_cashout = 2.00
             self.current_bet = 0.00
             self.game_state = 'idle'
             self.photo_url = ''
             return

        connection = create_db_connection()
        if not connection:
            logger.error(f"Could not connect to DB to load user {self.user_id}")
            return # Не можем загрузить данные без соединения

        try:
            cursor = connection.cursor()
            # Загружаем все необходимые колонки
            cursor.execute("""
            SELECT username, first_name, balance, auto_cashout, photo_url, current_bet, game_state
            FROM users WHERE user_id = %s
            """, (self.user_id,))
            user = cursor.fetchone()

            if not user:
                logger.info(f"User {self.user_id} not found in DB, creating new.")
                # Создаем нового пользователя с начальными значениями
                if self.photo_id:
                    # Пытаемся получить URL фото при создании нового пользователя, если photo_id есть
                     self.photo_url = self.get_telegram_photo_url()

                cursor.execute("""
                INSERT INTO users (user_id, username, first_name, balance, auto_cashout, photo_url, current_bet, game_state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.user_id, self.username, self.first_name, self.balance, self.auto_cashout, self.photo_url, self.current_bet, self.game_state))
                connection.commit()
                logger.info(f"New user {self.user_id} created in DB.")
            else:
                logger.info(f"User {self.user_id} found in DB, loading data.")
                # Загружаем данные из БД
                self.username = user[0] if user[0] is not None else self.username # Используем загруженное значение, если не None
                self.first_name = user[1] if user[1] is not None else self.first_name
                self.balance = float(user[2])
                self.auto_cashout = float(user[3])
                self.photo_url = user[4] if user[4] is not None else ''
                self.current_bet = float(user[5]) # Загружено из БД
                self.game_state = user[6] if user[6] is not None else 'idle' # Загружено из БД

                # Обновляем фото, если оно изменилось или отсутствует в БД, но есть в user_data
                # Убедитесь, что self.photo_id здесь актуальный file_id из текущего initData
                if self.photo_id and (not self.photo_url or 'avatar.png' in self.photo_url):
                     logger.info(f"Updating photo for user {self.user_id}")
                     new_photo_url = self.get_telegram_photo_url()
                     if new_photo_url and new_photo_url != self.photo_url:
                         self.photo_url = new_photo_url
                         # Обновляем фото URL в БД в отдельной транзакции или в текущей перед коммитом
                         # Для простоты, можем обновить прямо здесь, если соединение активно
                         try:
                             cursor.execute("""
                                UPDATE users
                                SET photo_url = %s
                                WHERE user_id = %s
                             """, (self.photo_url, self.user_id))
                             connection.commit() # Коммитим только изменение фото
                             logger.info(f"Photo URL updated for user {self.user_id}.")
                         except Exception as e:
                             logger.error(f"Error updating photo URL for user {self.user_id} during load: {e}")
                             connection.rollback() # Откатываем только изменение фото, если не удалось

        except OperationalError as e:
            logger.error(f"Error loading or creating user {self.user_id} from DB: {e}")
            # Важно! Если загрузка не удалась, состояние пользователя в объекте останется дефолтным
            # или частично обновленным, что может привести к некорректной работе.
            # В продакшене нужно обрабатывать эту ошибку более надежно.
            if connection:
                connection.rollback() # Откатываем любые незавершенные операции, если была ошибка

        finally:
            if connection:
                connection.close()


    def save_game_state(self, connection):
        """
        Сохраняет состояние игры пользователя (баланс, текущая ставка, состояние игры) в базу данных.
        Принимает активное соединение с БД для использования в рамках транзакции.
        """
        if self.user_id == 0:
            logger.warning("Attempted to save state for user with ID 0. Skipping DB interaction.")
            return

        if not connection:
             logger.error(f"Cannot save state for user {self.user_id}: No DB connection provided.")
             raise OperationalError("No database connection") # Вызываем исключение для обработки ошибки выше

        try:
            cursor = connection.cursor()
            cursor.execute("""
                UPDATE users
                SET balance = %s, current_bet = %s, game_state = %s, updated_at = NOW()
                WHERE user_id = %s
            """, (self.balance, self.current_bet, self.game_state, self.user_id))
            # connection.commit() или rollback() должен быть вызван вызывающей функцией (маршрутом)
            logger.info(f"Saved state for user {self.user_id}: balance={self.balance}, bet={self.current_bet}, state={self.game_state}")

        except Exception as e:
            logger.error(f"Error saving user game state for user {self.user_id}: {e}")
            raise # Перевызываем исключение, чтобы вызывающая функция могла откатить транзакцию

    # TODO: Метод для сохранения auto_cashout, если фронтенд позволит его менять
    # def save_auto_cashout(self, connection, value):
    #     if self.user_id == 0 or not connection: return
    #     try:
    #          cursor = connection.cursor()
    #          cursor.execute("UPDATE users SET auto_cashout = %s WHERE user_id = %s", (value, self.user_id))
    #          self.auto_cashout = value # Обновляем объект
    #          logger.info(f"Updated auto_cashout for user {self.user_id} to {value}.")
    #     except Exception as e:
    #          logger.error(f"Error saving auto_cashout for user {self.user_id}: {e}")
    #          raise


    def get_telegram_photo_url(self):
        """Получает URL фото профиля из Telegram API"""
        # photo_id должен быть file_id самого большого фото профиля, полученный из initData
        if not self.photo_id or str(self.photo_id) == 'None' or str(self.photo_id) == '':
             logger.info(f"No photo_id available for user {self.user_id}. Cannot get photo URL.")
             return ''

        try:
            # Шаг 1: Получаем информацию о файле, чтобы узнать file_path
            file_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={self.photo_id}"
            logger.info(f"Requesting file info from TG: {file_info_url}")
            response = requests.get(file_info_url, timeout=5) # Добавлен таймаут
            response.raise_for_status() # Вызовет исключение для кодов ошибок HTTP (4xx, 5xx)
            result = response.json()

            if result.get('ok') and result.get('result'):
                 file_path = result['result'].get('file_path')
                 if file_path:
                     # Шаг 2: Формируем полный URL для загрузки файла
                     photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                     logger.info(f"Successfully got Telegram photo URL for {self.user_id}: {photo_url}")
                     return photo_url
                 else:
                     logger.warning(f"file_path not found in Telegram getFile response for {self.user_id}: {result}")
            else:
                 logger.warning(f"Telegram getFile response not OK for {self.user_id}: {result.get('description', 'No description')}. Response: {result}")

        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error getting Telegram photo URL for {self.user_id}: {e}")
        except Exception as e:
            logger.error(f"Error processing Telegram photo URL response for {self.user_id}: {e}")

        return ''


    def add_to_history(self, game_data, connection):
        """
        Добавляет запись в историю игр.
        Принимает активное соединение с БД для использования в рамках транзакции.
        """
        if self.user_id == 0:
            logger.warning("Attempted to add history for user with ID 0. Skipping DB interaction.")
            return

        if not connection:
             logger.error(f"Cannot add history for user {self.user_id}: No DB connection provided.")
             raise OperationalError("No database connection")

        try:
            cursor = connection.cursor()
            cursor.execute("""
            INSERT INTO game_history
            (user_id, game_type, bet_amount, win_amount, multiplier, result)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                self.user_id,
                game_data.get('game', 'unknown'), # Тип игры
                game_data.get('bet_amount', 0.0), # Сумма ставки
                game_data.get('win_amount', 0.0), # Сумма выигрыша
                game_data.get('multiplier', 1.0), # Множитель
                game_data.get('result', 'lose') # Результат ('win'/'lose')
            ))
            # connection.commit() или rollback() должен быть вызван вызывающей функцией (маршрутом)
            logger.info(f"Added history for user {self.user_id}. Game: {game_data.get('game')}, Result: {game_data.get('result')}, Bet: {game_data.get('bet_amount')}, Win: {game_data.get('win_amount')}")

        except Exception as e:
            logger.error(f"Error adding history for user {self.user_id}: {e}")
            raise # Перевызываем исключение

    def get_history(self, limit=15): # Увеличил лимит по умолчанию до 15 для соответствия фронтенду
        """Получает историю игр пользователя"""
        if self.user_id == 0:
             logger.warning("Attempted to get history for user with ID 0. Returning empty list.")
             return []

        connection = create_db_connection()
        if not connection:
            logger.error(f"Could not connect to DB to get history for user {self.user_id}")
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

# --- Маршруты Flask ---

# Маршрут для главной страницы (меню)
@app.route('/')
def home():
    # В идеале, здесь вы должны получить user_id из валидированных initData Telegram
    # Но в этом примере мы пока полагаемся на user_id из URL параметров,
    # как это было в вашем коде. Это НЕБЕЗОПАСНО для продакшена!
    user_id = request.args.get('user_id', type=int) # Получаем как int
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    # Если user_id не был передан или равен 0, это проблема инициализации Mini App
    # Создаем объект пользователя. Если ID будет 0 или некорректен, User.__init__ логирует это.
    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data) # Загружает или создает пользователя в БД (если ID не 0)

    # Получаем URL фото из объекта пользователя (теперь он загружен из БД или получен от TG)
    photo_url = user.photo_url

    logger.info(f"Rendering home page for user ID: {user.user_id}")
    # Передаем данные пользователя в шаблон
    return render_template('menu.html',
                           user_id=user.user_id, # Передаем user_id из объекта User (загружен из БД или 0)
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=photo_url)

# Маршрут для страницы Авиатора
@app.route('/aviator')
def aviator():
    # Аналогично главной странице, получаем данные пользователя из URL
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data) # Загружает или создает пользователя в БД (если ID не 0)

    photo_url = user.photo_url

    logger.info(f"Rendering aviator page for user ID: {user.user_id}")
    # Передаем данные пользователя в шаблон
    return render_template('aviator.html',
                           user_id=user.user_id, # Передаем user_id из объекта User
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=photo_url)

# Маршрут для страницы Сапера
@app.route('/mines')
def mines():
    # Аналогично, получаем данные пользователя из URL
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data) # Загружает или создает пользователя в БД (если ID не 0)

    photo_url = user.photo_url

    logger.info(f"Rendering mines page for user ID: {user.user_id}")
    # Передаем данные пользователя в шаблон
    return render_template('mines.html',
                           user_id=user.user_id,
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=photo_url)


# --- API Маршруты для взаимодействия фронтенда с бэкендом ---

# API для получения данных пользователя (используется фронтендом игры при инициализации)
@app.route('/api/user', methods=['POST'])
def get_user():
    try:
        data = request.get_json()
        # user_id приходит из userData фронтенда, полученного из URL
        user_id_from_frontend = data.get('user', {}).get('id')
        if user_id_from_frontend is None or (isinstance(user_id_from_frontend, (int, float)) and user_id_from_frontend == 0):
             logger.error(f"API /api/user: Invalid or missing user ID in request data: {user_id_from_frontend}.")
             return jsonify({'status': 'error', 'message': 'Invalid or missing user ID'}), 400

        user = User({'id': user_id_from_frontend}) # Загружаем данные пользователя по ID из запроса
        logger.info(f"API /api/user: Request for user ID: {user.user_id}")

        # Возвращаем данные пользователя фронтенду
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
        user_id_for_log = data.get('user', {}).get('id', 'N/A')
        logger.error(f"API /api/user error for user {user_id_for_log}: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

# API для получения фото профиля (если нужно получать отдельно, возможно, не используется)
@app.route('/api/user/photo', methods=['GET'])
def get_user_photo():
    photo_id = request.args.get('photo_id')
    if not photo_id:
        return jsonify({'status': 'error', 'message': 'No photo ID provided'}), 400

    # Создаем временный объект User для использования метода get_telegram_photo_url
    # User ID здесь не важен для этой функции, но конструктор User требует его
    # Передаем 0, но с фото_id
    temp_user = User({'id': 0, 'photo_id': photo_id, 'first_name': 'temp', 'username': 'temp'})
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

# API для обновления баланса (пример, может использоваться для пополнения)
@app.route('/api/user/update_balance', methods=['POST'])
def update_user_balance():
    connection = create_db_connection()
    if not connection:
         logger.error("API /api/user/update_balance: Database connection error.")
         return jsonify({'status': 'error', 'message': 'Database connection error'}), 500
    connection.autocommit = False # Начинаем транзакцию

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        amount = float(data.get('amount', 0))

        if user_id is None or (isinstance(user_id, (int, float)) and user_id == 0):
             logger.error(f"API /api/user/update_balance: Invalid or missing user ID: {user_id}.")
             connection.rollback()
             return jsonify({'status': 'error', 'message': 'Invalid or missing user ID'}), 400

        # Создаем объект User, чтобы загрузить текущий баланс
        # Передаем фиктивные значения для first_name/username, т.к. они не меняются здесь
        user = User({'id': user_id, 'first_name': 'temp', 'username': 'temp'})
        logger.info(f"API /api/user/update_balance: User {user.user_id}, amount: {amount}. Current balance: {user.balance}")

        # Обновляем баланс в объекте и сохраняем в БД
        user.balance += amount

        cursor = connection.cursor()
        cursor.execute("""
            UPDATE users
            SET balance = %s, updated_at = NOW()
            WHERE user_id = %s
        """, (user.balance, user.user_id))
        # В этом случае достаточно просто обновить баланс, без game_state/current_bet
        # user.save_game_state(connection) # Можно использовать и этот метод, но он обновляет все 3 поля

        connection.commit() # Подтверждаем транзакцию

        logger.info(f"API /api/user/update_balance: User {user.user_id}. New balance: {user.balance}")
        return jsonify({
            'status': 'success',
            'new_balance': user.balance
        })

    except Exception as e:
        user_id_for_log = data.get('user_id', 'N/A')
        logger.error(f"API /api/user/update_balance error for user {user_id_for_log}: {str(e)}")
        if connection:
             connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()


# API для Авиатора: Инициализация (получение баланса, истории, авто-вывода, состояния игры)
@app.route('/api/aviator/init', methods=['POST'])
def aviator_init():
    connection = create_db_connection() # Нужен коннект для возможного сохранения состояния
    if not connection:
        logger.error("API /api/aviator/init: Database connection error.")
        return jsonify({'status': 'error', 'message': 'Database connection error'}), 500
    connection.autocommit = False # Начинаем транзакцию

    try:
        data = request.get_json()
        # user_id приходит из userData фронтенда, полученного из URL
        user_id_from_frontend = data.get('user', {}).get('id')
        if user_id_from_frontend is None or (isinstance(user_id_from_frontend, (int, float)) and user_id_from_frontend == 0):
             logger.error(f"API /api/aviator/init: Invalid or missing user ID in request data: {user_id_from_frontend}.")
             connection.rollback()
             return jsonify({'status': 'error', 'message': 'Invalid or missing user ID'}), 400

        # Создаем объект User, который загружает данные пользователя из БД
        user = User({'id': user_id_from_frontend})
        logger.info(f"API /api/aviator/init: Request for user ID: {user.user_id}. Current state loaded: {user.game_state}, current bet: {user.current_bet}")

        # --- ЛОГИКА СБРОСА СОСТОЯНИЯ ---
        # Если пользователь находится в конечном состоянии ('cashed_out' или 'crashed'),
        # сбрасываем его состояние на 'idle', чтобы он мог сделать новую ставку в следующем раунде.
        # Это происходит при каждой инициализации страницы Авиатора, если пользователь не в активной игре.
        if user.game_state in ['cashed_out', 'crashed']:
             logger.info(f"API /api/aviator/init: Resetting state for user {user.user_id} from {user.game_state} to idle.")
             user.game_state = 'idle'
             user.current_bet = 0.00 # Убеждаемся, что ставка 0, когда состояние idle
             # Сохраняем сброшенное состояние в базу данных в рамках текущей транзакции
             user.save_game_state(connection)
        # --- КОНЕЦ ЛОГИКИ СБРОСА СОСТОЯНИЯ ---

        # Загружаем историю пользователя
        history = user.get_history()

        connection.commit() # Подтверждаем транзакцию (даже если состояние не менялось)

        # Возвращаем актуальные данные пользователя фронтенду
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': history,
            'auto_cashout': user.auto_cashout,
            'current_bet': user.current_bet, # Возвращаем текущую ставку (должна быть 0 после сброса или при idle)
            'game_state': user.game_state, # Возвращаем текущее состояние (должно быть 'idle' после сброса или при idle)
            'user': { # Возвращаем основные данные пользователя
                'id': user.user_id,
                'first_name': user.first_name,
                'username': user.username,
                'photo_url': user.photo_url
            }
        })
    except Exception as e:
        user_id_for_log = data.get('user', {}).get('id', 'N/A')
        logger.error(f"API /api/aviator/init error for user {user_id_for_log}: {str(e)}")
        if connection:
             connection.rollback() # Откатываем транзакцию при ошибке
        return jsonify({'status': 'error', 'message': 'Internal server error during initialization'}), 500
    finally:
        if connection:
            connection.close()


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
        # user_id приходит из userData фронтенда, полученного из URL
        user_id_from_frontend = data.get('user', {}).get('id')
        bet_amount = float(data.get('bet_amount', 0))

        if user_id_from_frontend is None or (isinstance(user_id_from_frontend, (int, float)) and user_id_from_frontend == 0):
             logger.error(f"API /api/aviator/bet: Invalid or missing user ID in request data: {user_id_from_frontend}.")
             connection.rollback()
             return jsonify({'status': 'error', 'message': 'Invalid or missing user ID'}), 400

        # Создаем объект User, который загружает данные пользователя из БД
        user = User({'id': user_id_from_frontend})
        logger.info(f"API /api/aviator/bet: Request for user ID: {user.user_id}, bet: {bet_amount}. Current state loaded: {user.game_state}, current bet: {user.current_bet}")


        # Проверка состояния игры пользователя: ставка разрешена только в состоянии 'idle'
        if user.game_state != 'idle':
            logger.warning(f"API /api/aviator/bet: User {user.user_id} tried to bet while state is {user.game_state}")
            connection.rollback() # Откатываем транзакцию
            return jsonify({'status': 'error', 'message': f'Игра в процессе. Текущее состояние: {user.game_state}'}), 400

        # Валидация ставки
        if bet_amount < 10:
            logger.warning(f"API /api/aviator/bet: User {user.user_id} tried to bet less than minimum ({bet_amount})")
            connection.rollback()
            return jsonify({'status': 'error', 'message': 'Минимальная ставка 10₽'}), 400

        if user.balance < bet_amount:
            logger.warning(f"API /api/aviator/bet: User {user.user_id} not enough balance ({user.balance}) for bet ({bet_amount})")
            connection.rollback()
            return jsonify({'status': 'error', 'message': 'Недостаточно средств'}), 400

        # Обновляем состояние пользователя в объекте User (пока только в памяти)
        user.balance -= bet_amount # Списываем ставку из баланса
        user.current_bet = bet_amount # Устанавливаем текущую ставку
        user.game_state = 'bet_placed' # Устанавливаем состояние "ставка сделана"

        # Сохраняем обновленное состояние в базу данных в рамках текущей транзакции
        user.save_game_state(connection)

        # Генерируем crash_point (это упрощение, в реальной игре он должен быть один на раунд для всех)
        # и должен быть сохранен где-то для раунда на сервере.
        # В этом примере, мы просто генерируем его и отдаем фронтенду.
        crash_point = round(random.uniform(1.1, 10.0), 2)

        connection.commit() # Подтверждаем транзакцию: баланс списан, ставка и состояние сохранены

        logger.info(f"API /api/aviator/bet: User {user.user_id} placed bet {bet_amount}. New balance: {user.balance}. State: {user.game_state}. Crash point: {crash_point}")
        # Возвращаем обновленные данные фронтенду
        return jsonify({
            'status': 'success',
            'balance': user.balance, # Возвращаем обновленный баланс
            'crash_point': crash_point, # В упрощенном варианте, генерируем и отдаем фронтенду
            'game_state': user.game_state # Возвращаем новое состояние ('bet_placed')
        })

    except Exception as e:
        user_id_for_log = data.get('user', {}).get('id', 'N/A')
        logger.error(f"API /api/aviator/bet error for user {user_id_for_log}: {str(e)}")
        if connection:
             connection.rollback() # Откатываем транзакцию при любой ошибке
        return jsonify({'status': 'error', 'message': 'Внутренняя ошибка сервера при ставке'}), 500
    finally:
        if connection:
            connection.close() # Закрываем соединение


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
        # user_id приходит из userData фронтенда, полученного из URL
        user_id_from_frontend = data.get('user', {}).get('id')
        multiplier = float(data.get('multiplier', 1.0))
        auto = data.get('auto', False) # Флаг авто-вывода (не используется в логике, только для истории)

        if user_id_from_frontend is None or (isinstance(user_id_from_frontend, (int, float)) and user_id_from_frontend == 0):
             logger.error(f"API /api/aviator/cashout: Invalid or missing user ID in request data: {user_id_from_frontend}.")
             connection.rollback()
             return jsonify({'status': 'error', 'message': 'Invalid or missing user ID'}), 400

        # Создаем объект User, который загружает данные пользователя из БД
        user = User({'id': user_id_from_frontend})
        logger.info(f"API /api/aviator/cashout: Request for user ID: {user.user_id}, multiplier: {multiplier}, auto: {auto}. Current state loaded: {user.game_state}, current bet: {user.current_bet}")

        # Проверка состояния игры пользователя: вывод разрешен только в состоянии 'bet_placed'
        # и только если есть активная ставка (> 0)
        if user.game_state != 'bet_placed' or user.current_bet <= 0:
            logger.warning(f"API /api/aviator/cashout: User {user.user_id} has no active bet (state: {user.game_state}, bet: {user.current_bet})")
            connection.rollback() # Откатываем транзакцию
            return jsonify({'status': 'error', 'message': 'Нет активной ставки'}), 400 # Это сообщение вернется фронтенду

        # Расчет выигрыша
        # Используем current_bet, загруженный из базы данных
        original_bet = float(user.current_bet) # Сохраняем сумму ставки до обнуления
        win_amount = int(original_bet * multiplier) # Округляем до целого рубля

        # Обновляем состояние пользователя в объекте User
        user.balance += win_amount # Добавляем выигрыш к балансу
        user.current_bet = 0.00 # Обнуляем текущую ставку
        user.game_state = 'cashed_out' # Устанавливаем состояние "выведено"

        # Сохраняем обновленное состояние в базу данных в рамках текущей транзакции
        user.save_game_state(connection)

        # Добавляем запись о выигрыше в историю игры в рамках той же транзакции
        history_data = {
            'game': 'aviator', # Тип игры
            'bet_amount': original_bet, # Сумма ставки
            'win_amount': win_amount, # Сумма выигрыша
            'multiplier': multiplier, # Множитель при выводе
            'result': 'win' # Результат - выигрыш
        }
        user.add_to_history(history_data, connection)

        connection.commit() # Подтверждаем транзакцию: баланс обновлен, ставка обнулена, состояние сохранено, история добавлена

        profit = win_amount - original_bet # Расчет прибыли для ответа
        logger.info(f"API /api/aviator/cashout: User {user.user_id} cashed out at {multiplier}x. Win: {win_amount}. Profit: {profit}. New balance: {user.balance}. State: {user.game_state}")

        # Возвращаем обновленные данные фронтенду
        return jsonify({
            'status': 'success',
            'balance': user.balance, # Возвращаем обновленный баланс
            'profit': profit, # Возвращаем прибыль
            'multiplier': multiplier,
            'auto': auto, # Возвращаем флаг авто-вывода (для информации на фронтенде)
            'game_state': user.game_state, # Возвращаем новое состояние ('cashed_out')
            # Можно также вернуть последнюю историю, если нужно сразу обновить список на фронтенде
            'history': user.get_history() # Загружаем и возвращаем обновленную историю
        })

    except Exception as e:
        user_id_for_log = data.get('user', {}).get('id', 'N/A')
        logger.error(f"API /api/aviator/cashout error for user {user_id_for_log}: {str(e)}")
        if connection:
             connection.rollback() # Откатываем транзакцию при любой ошибке
        # Если произошла ошибка после проверки ставки, но до коммита,
        # состояние в БД может остаться 'bet_placed'.
        # loadUserData в endGame на фронтенде поможет это синхронизировать в следующем раунде.
        return jsonify({'status': 'error', 'message': 'Внутренняя ошибка сервера при выводе средств'}), 500
    finally:
        if connection:
            connection.close() # Закрываем соединение

# API для Авиатора: Обработка крушения (когда самолет разбился)
# Этот маршрут обрабатывает ситуацию, когда пользователь не успел вывести средства.
# ЭТО УПРОЩЕННЫЙ МАРШРУТ! В реальной игре нужна система раундов и таймеров,
# которая сама определяет момент крушения для всех игроков.
@app.route('/api/aviator/crash', methods=['POST'])
def aviator_crash():
     connection = create_db_connection()
     if not connection:
         logger.error("API /api/aviator/crash: Database connection error.")
         return jsonify({'status': 'error', 'message': 'Database connection error'}), 500
     connection.autocommit = False # Начинаем транзакцию

     try:
         data = request.get_json()
         # user_id приходит из userData фронтенда, полученного из URL
         user_id_from_frontend = data.get('user', {}).get('id')
         crash_multiplier = float(data.get('multiplier', 1.0)) # Множитель, на котором произошло крушение

         if user_id_from_frontend is None or (isinstance(user_id_from_frontend, (int, float)) and user_id_from_frontend == 0):
              logger.error(f"API /api/aviator/crash: Invalid or missing user ID in request data: {user_id_from_frontend}.")
              connection.rollback()
              return jsonify({'status': 'error', 'message': 'Invalid or missing user ID'}), 400

         # Создаем объект User, который загружает данные пользователя из БД
         user = User({'id': user_id_from_frontend})
         logger.info(f"API /api/aviator/crash: Handling crash for user ID: {user.user_id} at {crash_multiplier}x. Current state loaded: {user.game_state}, current bet: {user.current_bet}")


         # Проверяем, была ли активная ставка и не успел ли пользователь вывести.
         # Состояние должно быть 'bet_placed' И текущая ставка > 0
         if user.game_state == 'bet_placed' and user.current_bet > 0:
              # Пользователь проиграл свою активную ставку
              original_bet = float(user.current_bet) # Сохраняем сумму ставки до обнуления
              user.current_bet = 0.00 # Обнуляем текущую ставку
              user.game_state = 'crashed' # Устанавливаем состояние "разбился"

              # Сохраняем обновленное состояние в базу данных в рамках текущей транзакции
              user.save_game_state(connection)

              # Добавляем запись о проигрыше в историю в рамках той же транзакции
              history_data = {
                  'game': 'aviator', # Тип игры
                  'bet_amount': original_bet, # Сумма ставки (которая проиграла)
                  'win_amount': 0.00, # Выигрыш 0
                  'multiplier': crash_multiplier, # Множитель в момент крушения
                  'result': 'lose' # Результат - проигрыш
              }
              user.add_to_history(history_data, connection)

              connection.commit() # Подтверждаем транзакцию: ставка обнулена, состояние сохранено, история добавлена

              logger.info(f"API /api/aviator/crash: User {user.user_id} lost bet {original_bet} at {crash_multiplier}x. New balance: {user.balance}. State: {user.game_state}")
              # Возвращаем обновленные данные фронтенду
              return jsonify({
                  'status': 'success',
                  'message': 'Bet lost',
                  'balance': user.balance, # Возвращаем текущий баланс (уже без ставки)
                  'lost_amount': original_bet, # Возвращаем сумму проигрыша
                  'game_state': user.game_state, # Возвращаем новое состояние ('crashed')
                  # Можно также вернуть последнюю историю, если нужно сразу обновить список на фронтенде
                  'history': user.get_history() # Загружаем и возвращаем обновленную историю
              })
         else:
              # Ставки не было, или пользователь уже вывел, или состояние некорректное. Ничего не делаем.
              # Это может случиться, если фронтенд отправил cashout, а потом crash почти одновременно,
              # или если пользователь зашел на страницу, когда раунд уже шел и разбился.
              logger.info(f"API /api/aviator/crash: User {user.user_id} had no active bet or already cashed out. State: {user.game_state}, bet: {user.current_bet}. No action needed.")
              connection.rollback() # Откатываем транзакцию, так как ничего не меняли
              # Возвращаем 200 OK, так как это не ошибка с точки зрения запроса, просто нет активной ставки для проигрыша
              return jsonify({'status': 'success', 'message': 'No active bet to lose or already processed'}), 200

     except Exception as e:
         user_id_for_log = data.get('user', {}).get('id', 'N/A')
         logger.error(f"API /api/aviator/crash error for user {user_id_for_log}: {str(e)}")
         if connection:
              connection.rollback() # Откатываем транзакцию при любой ошибке
         # Если произошла ошибка, и у пользователя была активная ставка, она может остаться в 'bet_placed'.
         # loadUserData в endGame на фронтенде поможет это синхронизировать в следующем раунде.
         return jsonify({'status': 'error', 'message': 'Внутренняя ошибка сервера при обработке крушения'}), 500
     finally:
         if connection:
             connection.close() # Закрываем соединение


# Точка входа для запуска приложения
if __name__ == '__main__':
    # Для локальной разработки: app.run(host='0.0.0.0', port=5000, debug=True)
    # Для продакшена на Render, используйте порт из переменной окружения и отключите debug
    logger.info("Starting Flask app...")
    port = int(os.environ.get('PORT', 5000)) # Получаем порт из переменной окружения PORT (для Render)
    # debug=True только для разработки. Отключите в продакшене!
    app.run(host='0.0.0.0', port=port, debug=False) # Установлен debug=False для продакшена
