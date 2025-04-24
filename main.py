from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import random
from collections import deque
import logging

app = Flask(__name__)
CORS(app)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# База данных в памяти
users_db = {}
mines_games = {}

class User:
    def __init__(self, user_id):
        self.user_id = user_id
        self.balance = 1000
        self.history = deque(maxlen=10)
        self.current_bet = 0
        self.auto_cashout = 2.0

# Маршруты для страниц
@app.route('/')
def home():
    return render_template('menu.html')

@app.route('/aviator')
def aviator():
    return render_template('aviator.html')

@app.route('/mines')
def mines():
    return render_template('mines.html')

# API для Авиатора
@app.route('/api/aviator/init', methods=['POST'])
def aviator_init():
    try:
        data = request.get_json()
        user_id = str(data['user']['id'])
        
        if user_id not in users_db:
            users_db[user_id] = User(user_id)
            logger.info(f"Created new user: {user_id}")
        
        user = users_db[user_id]
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': list(user.history),
            'auto_cashout': user.auto_cashout
        })
    except Exception as e:
        logger.error(f"Aviator init error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/aviator/bet', methods=['POST'])
def aviator_bet():
    try:
        data = request.get_json()
        user_id = str(data['user']['id'])
        bet_amount = int(data['bet_amount'])
        
        if user_id not in users_db:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user = users_db[user_id]
        
        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10'}), 400
        
        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400
        
        user.balance -= bet_amount
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
        user_id = str(data['user']['id'])
        multiplier = float(data['multiplier'])
        auto = data.get('auto', False)
        
        if user_id not in users_db:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user = users_db[user_id]
        
        if user.current_bet == 0:
            return jsonify({'status': 'error', 'message': 'No active bet'}), 400
        
        win_amount = int(user.current_bet * multiplier)
        user.balance += win_amount
        
        result = {
            'game': 'aviator',
            'multiplier': multiplier,
            'win': True,
            'amount': win_amount
        }
        
        user.history.appendleft(result)
        user.current_bet = 0
        
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': list(user.history),
            'auto_cashout': user.auto_cashout,
            'auto': auto
        })
    except Exception as e:
        logger.error(f"Aviator cashout error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/aviator/set_auto', methods=['POST'])
def aviator_set_auto():
    try:
        data = request.get_json()
        user_id = str(data['user']['id'])
        auto_cashout = float(data['auto_cashout'])
        
        if user_id not in users_db:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user = users_db[user_id]
        user.auto_cashout = auto_cashout
        
        return jsonify({
            'status': 'success',
            'auto_cashout': user.auto_cashout
        })
    except Exception as e:
        logger.error(f"Aviator set_auto error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

# API для игры Мины
@app.route('/api/mines/init', methods=['POST'])
def mines_init():
    try:
        data = request.get_json()
        
        # Изменение здесь - получаем user_id напрямую из data
        user_id = str(data.get('user', {}).get('id'))
  # Или data['user']['id'], если фронтенд пришлет так
        
        if not user_id:
            return jsonify({'status': 'error', 'message': 'User ID is required'}), 400
            
        if user_id not in users_db:
            users_db[user_id] = User(user_id)
            logger.info(f"Created new user: {user_id}")
        
        user = users_db[user_id]
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'history': list(user.history)
        })
    except Exception as e:
        logger.error(f"Mines init error: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500  # Показываем реальную ошибку

@app.route('/api/mines/bet', methods=['POST'])
def mines_bet():
    try:
        data = request.get_json()
        user_id = str(data['user']['id'])
        bet_amount = int(data['bet_amount'])
        mines_count = int(data.get('mines_count', 3))
        
        if user_id not in users_db:
            return jsonify({'status': 'error', 'message': 'User not found'}), 404
        
        user = users_db[user_id]
        
        if bet_amount < 10:
            return jsonify({'status': 'error', 'message': 'Minimum bet is 10'}), 400
        
        if user.balance < bet_amount:
            return jsonify({'status': 'error', 'message': 'Not enough balance'}), 400
        
        # Генерируем поле с минами
        mines = random.sample(range(25), mines_count)
        mines_field = [i in mines for i in range(25)]
        
        mines_games[user_id] = {
            'field': mines_field,
            'opened': [False]*25,
            'bet_amount': bet_amount,
            'mines_count': mines_count,
            'positions': mines  # Сохраняем позиции мин
        }
        
        user.balance -= bet_amount
        user.current_bet = bet_amount
        
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'bet_amount': bet_amount,
            'mines_count': mines_count,
            'positions': mines  # Отправляем позиции клиенту
        })
    except Exception as e:
        logger.error(f"Mines bet error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/mines/open', methods=['POST'])
def mines_open():
    try:
        data = request.get_json()
        user_id = str(data['user']['id'])
        cell_index = int(data['cell_index'])
        
        if user_id not in mines_games:
            return jsonify({'status': 'error', 'message': 'Game not started'}), 400
        
        game = mines_games[user_id]
        user = users_db[user_id]
        
        if game['opened'][cell_index]:
            return jsonify({'status': 'error', 'message': 'Cell already opened'}), 400
        
        game['opened'][cell_index] = True
        
        if game['field'][cell_index]:  # Если мина
            result = {
                'game': 'mines',
                'win': False,
                'amount': game['bet_amount'],
                'mines_count': game['mines_count']
            }
            user.history.appendleft(result)
            user.current_bet = 0
            return jsonify({
                'status': 'success',
                'result': 'lose',
                'balance': user.balance,
                'opened_cells': game['opened'],
                'mines_positions': game['positions']  # Все позиции мин
            })
        
        # Если не мина - рассчитываем текущий множитель
        opened_safe = sum(1 for i, opened in enumerate(game['opened']) if opened and not game['field'][i])
        total_safe = 25 - game['mines_count']
        multiplier = round(1 + (opened_safe / total_safe) * (game['mines_count'] * 0.5), 2)
        
        return jsonify({
            'status': 'success',
            'result': 'win',
            'multiplier': multiplier,
            'opened_cells': game['opened']
        })
    except Exception as e:
        logger.error(f"Mines open error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

@app.route('/api/mines/cashout', methods=['POST'])
def mines_cashout():
    try:
        data = request.get_json()
        user_id = str(data['user']['id'])
        
        if user_id not in mines_games:
            return jsonify({'status': 'error', 'message': 'Game not started'}), 400
        
        game = mines_games[user_id]
        user = users_db[user_id]
        
        opened_safe = sum(1 for i, opened in enumerate(game['opened']) if opened and not game['field'][i])
        total_safe = 25 - game['mines_count']
        multiplier = round(1 + (opened_safe / total_safe) * (game['mines_count'] * 0.5), 2)
        win_amount = int(game['bet_amount'] * multiplier)
        
        user.balance += win_amount
        result = {
            'game': 'mines',
            'win': True,
            'amount': win_amount,
            'multiplier': multiplier,
            'mines_count': game['mines_count']
        }
        user.history.appendleft(result)
        user.current_bet = 0
        
        return jsonify({
            'status': 'success',
            'balance': user.balance,
            'win_amount': win_amount,
            'multiplier': multiplier,
            'opened_cells': game['opened'],
            'mines_positions': game['positions']  # Все позиции мин
        })
    except Exception as e:
        logger.error(f"Mines cashout error: {str(e)}")
        return jsonify({'status': 'error', 'message': 'Internal server error'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)