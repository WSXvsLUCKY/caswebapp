#================================================================#
                        #Импорты
#================================================================#

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import random
import logging
import psycopg2
from psycopg2 import OperationalError
import os
from datetime import datetime
import requests 


#===============================================================#
                        # Конфигурация
#===============================================================#
app = Flask(__name__)
CORS(app)

BOT_TOKEN = '8056124415:AAFIAyBtNxploGdE8Auk2i6m15gruZSLyoQ'
POSTGRES_CONFIG = {
    'host': 'dpg-d0gutm3e5dus73an8ol0-a.oregon-postgres.render.com',
    'port': 5432,  # Стандартный порт PostgreSQL, если не указан явно в URL
    'database': 'dbname_5irz',
    'user': 'user_data',
    'password': '6ekqhYE3rjIpmKSSCnBbMIfjsqYuu0Ts'
}
#===============================================================#
                    # Настройка логирования
#===============================================================#
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


#===============================================================#
                    # Создание бд
#===============================================================#
def create_db_connection():
    """Создает соединение с PostgreSQL базой данных"""
    try:
        connection = psycopg2.connect(**POSTGRES_CONFIG)
        return connection
    except OperationalError as e:
        logger.error(f"Ошибка подключение к PostgreSQL: {e}")
        return None

def init_db():
    """Инициализация базы данных и таблиц"""
    connection = create_db_connection()
    if not connection:
        return False

    try:
        cursor = connection.cursor()

        # Сначала создаем таблицу пользователей, если ее нет
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username VARCHAR(255),
            first_name VARCHAR(255),
            balance DECIMAL(15, 2) DEFAULT 0.00,
            referral_balance DECIMAL(10, 2) DEFAULT 0.00,
            referrals BIGINT[] DEFAULT '{}',
            referrer BIGINT,
            games_played BIGINT DEFAULT 0,
            wins BIGINT DEFAULT 0,
            losses BIGINT DEFAULT 0,
            total_bets DECIMAL(12, 2) DEFAULT 0.00,
            total_wins_amount DECIMAL(12, 2) DEFAULT 0.00,
            total_lose_amount DECIMAL(12, 2) DEFAULT 0.00,
            last_game_state JSONB,
            auto_cashout DECIMAL(5, 2) DEFAULT 2.00,
            photo_url VARCHAR(512),
            current_bet DECIMAL(15, 2) DEFAULT 0.00,
            game_state VARCHAR(50) DEFAULT 'idle',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Затем создаем таблицу истории игр
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

        # Добавляем триггер для обновления времени изменения
        cursor.execute("""
        CREATE OR REPLACE FUNCTION update_timestamp()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_trigger 
                WHERE tgname = 'update_users_timestamp'
            ) THEN
                CREATE TRIGGER update_users_timestamp
                BEFORE UPDATE ON users
                FOR EACH ROW
                EXECUTE FUNCTION update_timestamp();
            END IF;
        END
        $$;
        """)

        # Теперь добавляем колонки, если их нет (для обратной совместимости)
        cursor.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='users' AND column_name='current_bet'
            ) THEN
                ALTER TABLE users ADD COLUMN current_bet DECIMAL(15, 2) DEFAULT 0.00;
            END IF;
            
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='users' AND column_name='game_state'
            ) THEN
                ALTER TABLE users ADD COLUMN game_state VARCHAR(50) DEFAULT 'idle';
            END IF;
            
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name='users' AND column_name='photo_url'
            ) THEN
                ALTER TABLE users ADD COLUMN photo_url VARCHAR(512);
            END IF;
        END
        $$;
        """)

        connection.commit()
        logger.info("Database tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        connection.rollback()
        return False
    finally:
        if connection:
            connection.close()
init_db()
#===============================================================#
                            #Класс юзера
#===============================================================#

class User:
    def __init__(self, user_data):
        self.user_id = int(user_data['id']) if isinstance(user_data['id'], (str, float)) and str(user_data['id']).isdigit() else user_data['id']
        self.username = user_data.get('username', '')
        self.first_name = user_data.get('first_name', 'User')
        self.photo_id = user_data.get('photo_id', '')

        self.balance = 0.00
        self.auto_cashout = 2.00
        self.current_bet = 0.00
        self.game_state = 'idle'
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
            SELECT username, first_name, balance, auto_cashout, photo_url, current_bet, game_state
            FROM users WHERE user_id = %s
            """, (self.user_id,))
            user = cursor.fetchone()

            if not user:
                logger.info(f"User {self.user_id} not found in DB, creating new.")
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
                self.current_bet = float(user[5])
                self.game_state = user[6] or 'idle'

                if self.photo_id and (not self.photo_url or 'avatar.png' in self.photo_url):
                    self.photo_url = self.get_telegram_photo_url()
                    if self.photo_url:
                        self.update_photo_url(connection)

        except OperationalError as e:
            logger.error(f"Error loading or creating user {self.user_id} from DB: {e}")
            if connection:
                connection.rollback()
        finally:
            if connection:
                connection.close()
    #=============================================================================
                # РАБОТА С СТАТОЙ
    #=============================================================================
    def increment_games_played(self, user_id: int):
        """Увеличивает счетчик сыгранных игр"""
        connection = create_db_connection()
        if not connection:
            return
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET games_played = games_played + 1 WHERE user_id = %s", (user_id,))
            connection.commit()
        except Exception as e:
            logger.error(f"Error incrementing games played: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def increment_wins(self, user_id: int):
        """Увеличивает счетчик выигрышей"""
        connection = create_db_connection()
        if not connection:
            return
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET wins = wins + 1 WHERE user_id = %s", (user_id,))
            connection.commit()
        except Exception as e:
            logger.error(f"Error incrementing wins: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def increment_losses(self, user_id: int):
        """Увеличивает счетчик проигрышей"""
        connection = create_db_connection()
        if not connection:
            return
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET losses = losses + 1 WHERE user_id = %s", (user_id,))
            connection.commit()
        except Exception as e:
            logger.error(f"Error incrementing losses: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def update_total_bets(self, user_id: int, amount: float):
        """Обновляет общую сумму ставок"""
        connection = create_db_connection()
        if not connection:
            return
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET total_bets = total_bets + %s WHERE user_id = %s", (amount, user_id))
            connection.commit()
        except Exception as e:
            logger.error(f"Error updating total bets: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def update_total_wins_amount(self, user_id: int, amount: float):
        """Обновляет общую сумму выигрышей"""
        connection = create_db_connection()
        if not connection:
            return
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET total_wins_amount = total_wins_amount + %s WHERE user_id = %s", (amount, user_id))
            connection.commit()
        except Exception as e:
            logger.error(f"Error updating total wins amount: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def update_total_lose_amount(self, user_id: int, amount: float):
        """Обновляет общую сумму проигрышей"""
        connection = create_db_connection()
        if not connection:
            return
        try:
            cursor = connection.cursor()
            cursor.execute("UPDATE users SET total_lose_amount = total_lose_amount + %s WHERE user_id = %s", (amount, user_id))
            connection.commit()
        except Exception as e:
            logger.error(f"Error updating total lose amount: {e}")
            connection.rollback()
        finally:
            if connection:
                connection.close()

    def get_telegram_photo_url(self):
        """Получает URL фото профиля из Telegram"""
        if not self.photo_id or self.photo_id == 'None':
            return ''

        try:
            file_info_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getFile?file_id={self.photo_id}"
            response = requests.get(file_info_url, timeout=5)
            response.raise_for_status()
            result = response.json()

            if result.get('ok') and result.get('result'):
                file_path = result['result'].get('file_path')
                if file_path:
                    photo_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                    logger.info(f"Got Telegram photo URL for {self.user_id}: {photo_url}")
                    return photo_url
        except Exception as e:
            logger.error(f"Error getting Telegram photo URL for {self.user_id}: {e}")
        return ''

    def update_photo_url(self, connection):
        """Обновляет URL фото в базе данных"""
        if not self.photo_url or not connection:
            return

        try:
            cursor = connection.cursor()
            cursor.execute("""
            UPDATE users SET photo_url = %s WHERE user_id = %s
            """, (self.photo_url, self.user_id))
        except Exception as e:
            logger.error(f"Error updating photo URL for user {self.user_id}: {e}")
            raise

    def save_game_state(self, connection):
        """Сохраняет состояние игры пользователя"""
        if not connection:
            logger.error(f"Cannot save state for user {self.user_id}: No DB connection.")
            raise OperationalError("No database connection")

        try:
            cursor = connection.cursor()
            cursor.execute("""
                UPDATE users
                SET balance = %s, current_bet = %s, game_state = %s
                WHERE user_id = %s
            """, (self.balance, self.current_bet, self.game_state, self.user_id))
            logger.info(f"Saved state for user {self.user_id}")
        except Exception as e:
            logger.error(f"Error saving user game state: {e}")
            raise

    def add_to_history(self, game_data, connection):
        """Добавляет запись в историю игр"""
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
                game_data.get('result', 'lose')
            ))
            logger.info(f"Added history for user {self.user_id}")
        except Exception as e:
            logger.error(f"Error adding history: {e}")
            raise

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

#===============================================================#
                    # ГЛАВНОЕ МЕНЮ
#===============================================================#
@app.route('/')
def home():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
        logger.warning("User ID is missing or 0 in the initial request URL.")
        user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data)

    return render_template('menu.html',
                         user_id=user.user_id,
                         username=user.username,
                         first_name=user.first_name,
                         photo_url=user.photo_url)

#===============================================================#
                            # АВИАТОР 
#===============================================================#

@app.route('/aviator')
def aviator():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
        logger.warning("User ID is missing or 0 in aviator page URL.")
        user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data)

    return render_template('aviator.html',
                         user_id=user.user_id,
                         username=user.username,
                         first_name=user.first_name,
                         photo_url=user.photo_url)

# API для работы с пользователем
@app.route('/api/user', methods=['POST'])
def get_user():
    try:
        data = request.get_json()
        user_id = data.get('user', {}).get('id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})
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

# API для Авиатора
@app.route('/api/aviator/init', methods=['POST'])
def aviator_init():
    try:
        data = request.get_json()
        user_id = data.get('user', {}).get('id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})
        history = user.get_history()

        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': history,
            'auto_cashout': user.auto_cashout,
            'current_bet': user.current_bet,
            'game_state': user.game_state,
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

@app.route('/api/aviator/bet', methods=['POST'])
def aviator_bet():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user', {}).get('id')
        bet_amount = float(data.get('bet_amount', 0))

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'idle':
            return jsonify({'status': 'error', 'message': f'Game in progress. Current state: {user.game_state}'}), 400

        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10₽'}), 400

        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400

        user.balance -= bet_amount
        user.current_bet = bet_amount
        user.game_state = 'bet_placed'
        # Обновляем статистику
        user.increment_games_played(user_id)
        user.update_total_bets(user_id, bet_amount)

        user.save_game_state(connection)

        crash_point = round(random.uniform(1.1, 10.0), 2)

        connection.commit()

        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'crash_point': crash_point
        })
    except Exception as e:
        logger.error(f"API /api/aviator/bet error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/aviator/cashout', methods=['POST'])
def aviator_cashout():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user', {}).get('id')
        multiplier = float(data.get('multiplier', 1.0))
        auto = data.get('auto', False)

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'bet_placed' or user.current_bet <= 0:
            return jsonify({'status': 'error', 'message': 'No active bet'}), 400

        win_amount = int(user.current_bet * multiplier)
        original_bet = float(user.current_bet)
        
        user.balance += win_amount
        user.current_bet = 0.00
        user.game_state = 'idle'
        # Обновляем статистику
        user.increment_wins(user_id)
        user.update_total_wins_amount(user_id, win_amount)

        user.save_game_state(connection)

        history_data = {
            'game': 'aviator',
            'bet_amount': original_bet,
            'win_amount': win_amount,
            'multiplier': multiplier,
            'result': 'win'
        }
        user.add_to_history(history_data, connection)

        connection.commit()

        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'profit': win_amount - original_bet,
            'multiplier': multiplier,
            'auto': auto,
            'game_state': 'idle'
        })
    except Exception as e:
        logger.error(f"API /api/aviator/cashout error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/aviator/crash', methods=['POST'])
def aviator_crash():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user', {}).get('id')
        crash_multiplier = float(data.get('multiplier', 1.0))

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state == 'bet_placed' and user.current_bet > 0:
            original_bet = float(user.current_bet)
            user.current_bet = 0.00
            user.game_state = 'idle'
            
            # Обновляем статистику
            user.increment_losses(user_id)
            user.update_total_lose_amount(user_id, original_bet)

            user.save_game_state(connection)

            history_data = {
                'game': 'aviator',
                'bet_amount': original_bet,
                'win_amount': 0.00,
                'multiplier': crash_multiplier,
                'result': 'lose'
            }
            user.add_to_history(history_data, connection)

            connection.commit()

            return jsonify({
                'status': 'success',
                'balance': user.balance,
                'lost_amount': original_bet,
                'game_state': 'idle'
            })
        else:
            connection.rollback()
            return jsonify({'status': 'success', 'message': 'No active bet to lose'}), 200

    except Exception as e:
        logger.error(f"API /api/aviator/crash error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/aviator/reset', methods=['POST'])
def aviator_reset():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user', {}).get('id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})
        
        user.game_state = 'idle'
        user.current_bet = 0.00
        user.save_game_state(connection)
        
        connection.commit()
        
        return jsonify({
            'status': 'success',
            'game_state': 'idle'
        })
    except Exception as e:
        logger.error(f"API /api/aviator/reset error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

#===============================================================#
                   # МИНЫ
#===============================================================#

@app.route('/mines')
def mines():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
        logger.warning("User ID is missing or 0 in mines page URL.")
        user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data)

    return render_template('mines.html',
                           user_id=user.user_id,
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=user.photo_url,
                           initial_balance=user.balance)

    return render_template('mines.html' )
@app.route('/api/mines/bet', methods=['POST'])
def mines_bet():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        bet_amount = float(data.get('bet_amount', 0))
        mines_count = int(data.get('mines_count', 3))

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'idle':
            return jsonify({'status': 'error', 'message': f'Game in progress. Current state: {user.game_state}'}), 400

        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10₽'}), 400

        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400

        user.balance -= bet_amount
        user.current_bet = bet_amount
        user.game_state = 'bet_placed_mines'

        # Обновляем статистику
        user.increment_games_played(user_id)
        user.update_total_bets(user_id, bet_amount)

        user.save_game_state(connection)

        connection.commit()

        return jsonify({'status': 'success', 'balance': user.balance})

    except Exception as e:
        logger.error(f"API /api/mines/bet error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/mines/reveal', methods=['POST'])
def mines_reveal():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        index = int(data.get('index'))
        mine_positions = data.get('minePositions') # Передавайте с фронтенда

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'bet_placed_mines':
            return jsonify({'status': 'error', 'message': 'No active game'}), 400

        is_mine = index in mine_positions

        if is_mine:
            # Обновляем статистику
            user.increment_losses(user_id)
            user.update_total_lose_amount(user_id, user.current_bet)

            user.game_state = 'lost_mines'
            history_data = {
                'game': 'mines',
                'bet_amount': user.current_bet,
                'win_amount': 0.00,
                'multiplier': 0.00,
                'result': 'lose'
            }
            user.add_to_history(history_data, connection)
            user.current_bet = 0.00
            user.save_game_state(connection)
            connection.commit()
            return jsonify({'status': 'mine', 'balance': user.balance})
        else:
            # Рассчитайте множитель на основе количества открытых ячеек (нужно реализовать логику)
            revealed_count = int(data.get('revealedCells')) + 1
            mines_count = int(data.get('minesCount'))
            multiplier = calculate_mines_multiplier(revealed_count, mines_count) # Пример функции
            return jsonify({'status': 'safe', 'multiplier': multiplier})

    except Exception as e:
        logger.error(f"API /api/mines/reveal error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

#===============================================================#
                    # КЭФЫ мин
#===============================================================#

def calculate_mines_multiplier(revealed, total_mines):
    multipliers_by_mines = {
        3: [1.1, 1.3, 1.6, 2.0, 2.5, 3.2, 4.1, 5.3, 6.8, 8.6, 11, 14, 18],
        5: [1.15, 1.4, 1.8, 2.4, 3.2, 4.5, 6, 8, 11, 15, 20, 27, 35],
        7: [1.2, 1.6, 2.1, 3.0, 4.2, 6.0, 8.5, 12, 17, 23, 30, 38, 50]
    }
    if total_mines in multipliers_by_mines:
        if revealed < len(multipliers_by_mines[total_mines]):
            return multipliers_by_mines[total_mines][revealed]
        elif len(multipliers_by_mines[total_mines]) > 0:
            return multipliers_by_mines[total_mines][-1]
    return 1.0

@app.route('/api/mines/cashout', methods=['POST'])
def mines_cashout():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        multiplier = float(data.get('multiplier', 1.0)) # Получайте текущий множитель с фронтенда

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'bet_placed_mines':
            return jsonify({'status': 'error', 'message': 'No active game to cash out'}), 400

        win_amount = user.current_bet * multiplier
        original_bet = user.current_bet

        user.balance += win_amount
        user.current_bet = 0.00
        user.game_state = 'idle'

        # Обновляем статистику
        user.increment_wins(user_id)
        user.update_total_wins_amount(user_id, win_amount)
        
        user.save_game_state(connection)

        history_data = {
            'game': 'mines',
            'bet_amount': original_bet,
            'win_amount': win_amount,
            'multiplier': multiplier,
            'result': 'win'
        }
        user.add_to_history(history_data, connection)

        connection.commit()

        return jsonify({'status': 'success', 'balance': user.balance, 'win_amount': win_amount})

    except Exception as e:
        logger.error(f"API /api/mines/cashout error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/mines/reset', methods=['POST'])
def mines_reset():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})
        user.game_state = 'idle'
        user.current_bet = 0.00
        user.save_game_state(connection)
        connection.commit()

        return jsonify({'status': 'success', 'game_state': 'idle'})

    except Exception as e:
        logger.error(f"API /api/mines/reset error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

#===============================================================#
                    # КУБЫ
#===============================================================#

@app.route('/cubes')
def kub():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
        logger.warning("User ID is missing or 0 in kub page URL.")
        user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data)

    return render_template('cubes.html',
                           user_id=user.user_id,
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=user.photo_url,
                           initial_balance=user.balance)

@app.route('/api/cubes/bet', methods=['POST'])
def kub_bet():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        bet_amount = float(data.get('bet_amount', 0))
        selected_bet = data.get('selectedBet')

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'idle':
            return jsonify({'status': 'error', 'message': f'Game in progress. Current state: {user.game_state}'}), 400

        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10₽'}), 400

        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400

        if not selected_bet:
            return jsonify({'status': 'error', 'message': 'Select a bet type'}), 400

        user.balance -= bet_amount
        user.current_bet = bet_amount
        user.game_state = 'bet_placed_kub'
        # Обновляем статистику
        user.increment_games_played(user_id)
        user.update_total_bets(user_id, bet_amount)
        user.save_game_state(connection)

        connection.commit()

        return jsonify({'status': 'success', 'balance': user.balance})

    except Exception as e:
        logger.error(f"API /api/cubes/bet error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/cubes/roll', methods=['POST'])
def kub_roll():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        selected_bet = data.get('selectedBet')

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'bet_placed_kub':
            return jsonify({'status': 'error', 'message': 'No active bet'}), 400

        dice_value = random.randint(1, 6)
        win = False
        multiplier = 0

        if selected_bet == 'even' and dice_value % 2 == 0:
            win = True
            multiplier = 2
        elif selected_bet == 'odd' and dice_value % 2 != 0:
            win = True
            multiplier = 2
        elif selected_bet == 'less' and dice_value < 4:
            win = True
            multiplier = 1.5
        elif selected_bet == 'more' and dice_value > 3:
            win = True
            multiplier = 1.5
        elif selected_bet == 'duel1-3' and (dice_value == 1 or dice_value == 3):
            win = True
            multiplier = 3
        elif selected_bet == 'duel2-4' and (dice_value == 2 or dice_value == 4):
            win = True
            multiplier = 3
        elif selected_bet == 'duel5-6' and (dice_value == 5 or dice_value == 6):
            win = True
            multiplier = 3
        elif selected_bet == 'exact1' and dice_value == 1:
            win = True
            multiplier = 6
        elif selected_bet == 'exact6' and dice_value == 6:
            win = True
            multiplier = 6

        win_amount = 0
        original_bet = float(user.current_bet)

        if win:
            # Обновляем статистику
            user.increment_wins(user_id)
            user.update_total_wins_amount(user_id, win_amount)
            win_amount = original_bet * multiplier
            user.balance += win_amount
            history_data = {
                'game': 'kub',
                'bet_amount': original_bet,
                'win_amount': win_amount,
                'multiplier': multiplier,
                'result': 'win'
            }
            user.add_to_history(history_data, connection)
        else:
            # Обновляем статистику
            user.increment_losses(user_id)
            user.update_total_lose_amount(user_id, original_bet)
            history_data = {
                'game': 'kub',
                'bet_amount': original_bet,
                'win_amount': 0.00,
                'multiplier': 0.00,
                'result': 'lose'
            }
            user.add_to_history(history_data, connection)

        user.current_bet = 0.00
        user.game_state = 'idle'
        user.save_game_state(connection)

        connection.commit()

        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'dice_value': dice_value,
            'win': win,
            'win_amount': win_amount
        })

    except Exception as e:
        logger.error(f"API /api/cubes/roll error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/cubes/reset', methods=['POST'])
def kub_reset():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400
        user = User({'id': user_id})
        user.game_state = 'idle'
        user.current_bet = 0.00
        user.save_game_state(connection)
        connection.commit()
        return jsonify({'status': 'success', 'game_state': 'idle'})
    except Exception as e:
        logger.error(f"API /api/cubes/reset error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()
            
#===============================================================#
                # БАШНЯ
#===============================================================#

@app.route('/tower')
def tower():
    user_id = request.args.get('user_id', type=int)
    username = request.args.get('username', '')
    first_name = request.args.get('first_name', 'Игрок')
    photo_id = request.args.get('photo_id', '')

    if not user_id:
        logger.warning("User ID is missing or 0 in tower page URL.")
        user_id = 0

    temp_user_data = {'id': user_id, 'username': username, 'first_name': first_name, 'photo_id': photo_id}
    user = User(temp_user_data)

    return render_template('royalmines.html',
                           user_id=user.user_id,
                           username=user.username,
                           first_name=user.first_name,
                           photo_url=user.photo_url,
                           initial_balance=user.balance)

@app.route('/api/tower/start', methods=['POST'])
def tower_start():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        bet_amount = float(data.get('bet_amount', 0))
        rows = int(data.get('rows', 6))
        cols = int(data.get('cols', 3))

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'idle':
            return jsonify({'status': 'error', 'message': f'Game in progress. Current state: {user.game_state}'}), 400

        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10₽'}), 400

        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400

        user.balance -= bet_amount
        user.current_bet = bet_amount
        user.game_state = 'playing_tower'

         # Обновляем статистику
        user.increment_games_played(user_id)
        user.update_total_bets(user_id, bet_amount)

        user.save_game_state(connection)

        mines_positions = [random.randint(0, cols - 1) for _ in range(rows)]

        connection.commit()

        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'rows': rows,
            'cols': cols,
            'mines_positions': mines_positions
        })

    except Exception as e:
        logger.error(f"API /api/tower/start error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/tower/select_cell', methods=['POST'])
def tower_select_cell():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        row = int(data.get('row'))
        col = int(data.get('col'))
        mines_positions = data.get('mines_positions')
        current_multiplier = float(data.get('current_multiplier'))

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'playing_tower':
            return jsonify({'status': 'error', 'message': 'No active game'}), 400

        is_mine = mines_positions[row] == col
        multiplier_increase = 0.25

        if is_mine:
             # Обновляем статистику
            user.increment_losses(user_id)
            user.update_total_lose_amount(user_id, user.current_bet)
            
            user.game_state = 'idle'
            history_data = {
                'game': 'tower',
                'bet_amount': user.current_bet,
                'win_amount': 0.00,
                'multiplier': current_multiplier,
                'result': 'lose'
            }
            user.add_to_history(history_data, connection)
            user.current_bet = 0.00
            user.save_game_state(connection)
            connection.commit()
            return jsonify({'status': 'mine', 'balance': user.balance})
        else:
            multiplier = current_multiplier + multiplier_increase
            return jsonify({'status': 'gem', 'multiplier': multiplier})

    except Exception as e:
        logger.error(f"API /api/tower/select_cell error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/tower/cash_out', methods=['POST'])
def tower_cash_out():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        multiplier = float(data.get('multiplier', 1.0))

        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400

        user = User({'id': user_id})

        if user.game_state != 'playing_tower':
            return jsonify({'status': 'error', 'message': 'No active game to cash out'}), 400

        win_amount = user.current_bet * multiplier
        original_bet = user.current_bet

        user.balance += win_amount
        user.current_bet = 0.00
        user.game_state = 'idle'

        # Обновляем статистику
        user.increment_wins(user_id)
        user.update_total_wins_amount(user_id, win_amount)
        user.save_game_state(connection)

        history_data = {
            'game': 'tower',
            'bet_amount': original_bet,
            'win_amount': win_amount,
            'multiplier': multiplier,
            'result': 'win'
        }
        user.add_to_history(history_data, connection)

        connection.commit()

        return jsonify({'status': 'success', 'balance': user.balance, 'win_amount': win_amount})

    except Exception as e:
        logger.error(f"API /api/tower/cash_out error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()

@app.route('/api/tower/reset', methods=['POST'])
def tower_reset():
    connection = create_db_connection()
    if not connection:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500
    connection.autocommit = False

    try:
        data = request.get_json()
        user_id = data.get('user_id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Missing user ID'}), 400
        user = User({'id': user_id})
        user.game_state = 'idle'
        user.current_bet = 0.00
        user.save_game_state(connection)
        connection.commit()
        return jsonify({'status': 'success', 'game_state': 'idle'})
    except Exception as e:
        logger.error(f"API /api/tower/reset error: {str(e)}")
        if connection:
            connection.rollback()
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500
    finally:
        if connection:
            connection.close()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)
