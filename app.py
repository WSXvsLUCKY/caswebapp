from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import random
import logging
import psycopg2
from psycopg2 import OperationalError
import os
from datetime import datetime
import requests

app = Flask(__name__)
CORS(app)

# Конфигурация
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
        
        # Проверяем существование колонки photo_url
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

        # Создаем таблицу пользователей (если не существует)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            first_name VARCHAR(255),
            balance DECIMAL(15, 2) DEFAULT 1000.00,
            auto_cashout DECIMAL(5, 2) DEFAULT 2.00,
            photo_url VARCHAR(512),
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
            result VARCHAR(10),
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
        self.user_id = user_data['id']
        self.username = user_data.get('username', '')
        self.first_name = user_data.get('first_name', 'User')
        self.photo_id = user_data.get('photo_id', '')
        self.balance = 1000.00
        self.auto_cashout = 2.00
        self.current_bet = 0
        self.photo_url = ''
        self.load_from_db()

    def load_from_db(self):
        """Загружает данные пользователя из базы данных"""
        connection = create_db_connection()
        if not connection:
            return
            
        try:
            cursor = connection.cursor()
            cursor.execute("""
            SELECT username, first_name, balance, auto_cashout, photo_url 
            FROM users WHERE user_id = %s
            """, (self.user_id,))
            user = cursor.fetchone()
            
            if not user:
                # Создаем нового пользователя
                if self.photo_id:
                    self.photo_url = self.get_telegram_photo_url()
                
                cursor.execute("""
                INSERT INTO users (user_id, username, first_name, balance, auto_cashout, photo_url)
                VALUES (%s, %s, %s, %s, %s, %s)
                """, (self.user_id, self.username, self.first_name, self.balance, self.auto_cashout, self.photo_url))
                connection.commit()
            else:
                self.username = user[0] or self.username
                self.first_name = user[1] or self.first_name
                self.balance = float(user[2])
                self.auto_cashout = float(user[3])
                self.photo_url = user[4] or ''
                
                # Обновляем фото, если оно изменилось
                if self.photo_id and not self.photo_url:
                    self.photo_url = self.get_telegram_photo_url()
                    self.update_photo_url()
        except OperationalError as e:
            logger.error(f"Error loading user from DB: {e}")
        finally:
            if connection:
                connection.close()

    def get_telegram_photo_url(self):
        """Получает URL фото профиля из Telegram"""
        if not self.photo_id:
            return ''
        
        try:
            # Получаем информацию о файле
            file_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={self.photo_id}"
            response = requests.get(file_info_url)
            if response.status_code == 200:
                file_path = response.json()['result']['file_path']
                return f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        except Exception as e:
            logger.error(f"Error getting Telegram photo URL: {e}")
        
        return ''

    def update_photo_url(self):
        """Обновляет URL фото в базе данных"""
        if not self.photo_url:
            return
            
        connection = create_db_connection()
        if not connection:
            return
            
        try:
            cursor = connection.cursor()
            cursor.execute("""
            UPDATE users 
            SET photo_url = %s 
            WHERE user_id = %s
            """, (self.photo_url, self.user_id))
            connection.commit()
        except OperationalError as e:
            logger.error(f"Error updating photo URL: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def update_balance(self, amount):
        """Обновляет баланс пользователя в базе данных"""
        connection = create_db_connection()
        if not connection:
            return False
            
        try:
            cursor = connection.cursor()
            cursor.execute("""
            UPDATE users 
            SET balance = balance + %s 
            WHERE user_id = %s
            RETURNING balance
            """, (amount, self.user_id))
            
            new_balance = cursor.fetchone()[0]
            connection.commit()
            self.balance = float(new_balance)
            return True
        except OperationalError as e:
            logger.error(f"Error updating balance: {e}")
            connection.rollback()
            return False
        finally:
            if connection:
                connection.close()

    def add_to_history(self, game_data):
        """Добавляет запись в историю игр"""
        connection = create_db_connection()
        if not connection:
            return
            
        try:
            cursor = connection.cursor()
            cursor.execute("""
            INSERT INTO game_history 
            (user_id, game_type, bet_amount, win_amount, multiplier, result)
            VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                self.user_id,
                game_data['game'],
                game_data.get('bet_amount', 0),
                game_data.get('win_amount', 0),
                game_data.get('multiplier', 1.0),
                'win' if game_data.get('win', False) else 'lose'
            ))
            connection.commit()
        except OperationalError as e:
            logger.error(f"Error adding to history: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

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
                
            return history
        except OperationalError as e:
            logger.error(f"Error getting history: {e}")
            return []
        finally:
            if connection:
                connection.close()

# Маршруты для страниц
@app.route('/')
def home():
    user_data = {
        'id': request.args.get('user_id', type=int) or 0,
        'username': request.args.get('username', ''),
        'first_name': request.args.get('first_name', 'Игрок'),
        'photo_id': request.args.get('photo_id', '')
    }
    
    # Получаем URL фото
    photo_url = ''
    if user_data['photo_id']:
        user = User(user_data)
        photo_url = user.photo_url
    
    return render_template('menu.html', 
                         user_id=user_data['id'],
                         username=user_data['username'],
                         first_name=user_data['first_name'],
                         photo_url=photo_url)

@app.route('/aviator')
def aviator():
    user_data = {
        'id': request.args.get('user_id', type=int) or 0,
        'username': request.args.get('username', ''),
        'first_name': request.args.get('first_name', 'Игрок'),
        'photo_id': request.args.get('photo_id', '')
    }
    
    # Получаем URL фото
    photo_url = ''
    if user_data['photo_id']:
        user = User(user_data)
        photo_url = user.photo_url
    
    return render_template('aviator.html', 
                         user_id=user_data['id'],
                         username=user_data['username'],
                         first_name=user_data['first_name'],
                         photo_url=photo_url)

@app.route('/mines')
def mines():
    user_data = {
        'id': request.args.get('user_id', type=int) or 0,
        'username': request.args.get('username', ''),
        'first_name': request.args.get('first_name', 'Игрок'),
        'photo_id': request.args.get('photo_id', '')
    }
    
    # Получаем URL фото
    photo_url = ''
    if user_data['photo_id']:
        user = User(user_data)
        photo_url = user.photo_url
    
    return render_template('mines.html', 
                         user_id=user_data['id'],
                         username=user_data['username'],
                         first_name=user_data['first_name'],
                         photo_url=photo_url)

# API для работы с пользователем
@app.route('/api/user', methods=['POST'])
def get_user():
    try:
        data = request.get_json()
        user = User(data['user'])
        
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
        logger.error(f"User data error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/user/photo', methods=['GET'])
def get_user_photo():
    photo_id = request.args.get('photo_id')
    if not photo_id:
        return jsonify({'status': 'error', 'message': 'No photo ID provided'}), 400
    
    try:
        # Получаем информацию о файле
        file_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={photo_id}"
        response = requests.get(file_info_url)
        
        if response.status_code != 200:
            return jsonify({'status': 'error', 'message': 'Failed to get file info'}), 400
            
        file_path = response.json()['result']['file_path']
        photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
        
        return jsonify({
            'status': 'success',
            'photo_url': photo_url
        })
    except Exception as e:
        logger.error(f"Error getting user photo: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Failed to get photo'}), 500

@app.route('/api/user/update_balance', methods=['POST'])
def update_user_balance():
    try:
        data = request.get_json()
        user = User({
            'id': data['user_id'],
            'first_name': 'Temp',
            'username': 'temp'
        })
        
        amount = float(data['amount'])
        if not user.update_balance(amount):
            return jsonify({'status': 'error', 'message': 'Database error'}), 500
            
        return jsonify({
            'status': 'success',
            'new_balance': user.balance
        })
    except Exception as e:
        logger.error(f"Update balance error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

# API для Авиатора
@app.route('/api/aviator/init', methods=['POST'])
def aviator_init():
    try:
        data = request.get_json()
        user = User(data['user'])
        history = user.get_history()
        
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': history,
            'auto_cashout': user.auto_cashout,
            'user': {
                'id': user.user_id,
                'first_name': user.first_name,
                'username': user.username,
                'photo_url': user.photo_url
            }
        })
    except Exception as e:
        logger.error(f"Aviator init error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/aviator/bet', methods=['POST'])
def aviator_bet():
    try:
        data = request.get_json()
        user = User(data['user'])
        bet_amount = float(data['bet_amount'])
        
        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10'}), 400
        
        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400
        
        if not user.update_balance(-bet_amount):
            return jsonify({'status': 'error', 'message': 'Database error'}), 500
            
        user.current_bet = bet_amount
        crash_point = round(random.uniform(1.1, 10.0), 2)
        
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'crash_point': crash_point
        })
    except Exception as e:
        logger.error(f"Aviator bet error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/aviator/cashout', methods=['POST'])
def aviator_cashout():
    try:
        data = request.get_json()
        user = User(data['user'])
        multiplier = float(data['multiplier'])
        auto = data.get('auto', False)
        
        if user.current_bet == 0:
            return jsonify({'status': 'error', 'message': 'No active bet'}), 400
        
        win_amount = int(user.current_bet * multiplier)
        
        if not user.update_balance(win_amount):
            return jsonify({'status': 'error', 'message': 'Database error'}), 500
        
        result = {
            'game': 'aviator',
            'bet_amount': user.current_bet,
            'win_amount': win_amount,
            'multiplier': multiplier,
            'win': True
        }
        user.add_to_history(result)
        user.current_bet = 0
        
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': user.get_history(),
            'auto_cashout': user.auto_cashout,
            'auto': auto
        })
    except Exception as e:
        logger.error(f"Aviator cashout error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
