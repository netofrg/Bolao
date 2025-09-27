from flask import Flask, render_template, request, redirect, url_for, flash, session
from pymongo import MongoClient
from flask_bcrypt import Bcrypt
import os
from dotenv import load_dotenv
from bson.objectid import ObjectId
from functools import wraps
from datetime import datetime, date  # Importado 'date' para uso em now_date()
from bson.errors import InvalidId # <-- ADICIONE ESTA IMPORTAÇÃO
import base64
from werkzeug.utils import secure_filename

# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# --- Configuração do Flask e MongoDB ---
app = Flask(__name__)
# Chave secreta obtida do .env
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
bcrypt = Bcrypt(app)

# Conexão com o MongoDB
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
client = MongoClient(MONGO_URI)
db = client.bolao_brasileirao  # Nome do nosso banco de dados

# COLEÇÕES
usuarios_collection = db.apostadores
times_collection = db.times
rodadas_collection = db.rodadas
palpites_collection = db.palpites
ranking_collection = db.ranking  # Coleção para armazenar a pontuação por rodada

# --- Variável para o formato de data/hora salvo pelo campo datetime-local ---
# Formato: YYYY-MM-DDTHH:MM (ex: 2025-10-26T18:00)
DATETIME_FORMAT = '%Y-%m-%dT%H:%M'


# --- Funções Auxiliares de Serialização e Segurança ---
# --- FUNÇÃO AUXILIAR NECESSÁRIA PARA O MONGO/JINJA2 ---
def serialize_mongo_object(data):
    """Converte ObjectIds em strings em um dicionário ou lista."""
    if isinstance(data, dict):
        # Converte o ObjectId principal
        if '_id' in data:
            data['_id'] = str(data['_id'])
        # Itera sobre outros campos aninhados, se necessário
        for key, value in data.items():
            data[key] = serialize_mongo_object(value)
        return data
    elif isinstance(data, list):
        return [serialize_mongo_object(item) for item in data]
    return data
# --- FUNÇÃO SOLUÇÃO PARA O 'NameError' ---
def get_time_by_id(time_id):
    """Busca os dados completos do time, incluindo escudo_base64."""
    if not time_id:
        return {'nome': 'Time Inválido', 'sigla': '???', 'escudo_base64': None}
        
    try:
        # Tenta converter o ID para ObjectId
        time_object_id = ObjectId(time_id)
    except InvalidId:
        return {'nome': 'Time Inválido', 'sigla': 'ID Inválido', 'escudo_base64': None}
        
    # **times_collection** deve ser sua variável de coleção MongoDB para times
    time_data = times_collection.find_one({'_id': time_object_id})
    
    # Retorna o dicionário completo do time (incluindo escudo_base64)
    return serialize_mongo_object(time_data) if time_data else {'nome': 'Time Desconhecido', 'sigla': 'N/A', 'escudo_base64': None}

def image_to_base64(file_storage):
    """Converte um objeto FileStorage para uma string Base64 no formato Data URL."""
    
    # CRÍTICO: Se o arquivo não existe ou não tem nome, retorna None (salva 'null' no DB)
    if not file_storage or not file_storage.filename:
        print("DEBUG_BASE64: Nenhum arquivo válido fornecido.")
        return None
        
    try:
        # Tenta voltar o cursor de leitura para o início
        file_storage.seek(0)
        file_bytes = file_storage.read()
        
        # CRÍTICO: Verifica se o arquivo lido não está vazio
        if not file_bytes:
            print("DEBUG_BASE64: O arquivo foi recebido, mas está vazio após a leitura.")
            return None
        
        base64_bytes = base64.b64encode(file_bytes)
        base64_string = base64_bytes.decode('utf-8')
        
        print(f"DEBUG_BASE64: Base64 gerado com {len(base64_string)} caracteres.")

        mime_type = file_storage.mimetype
        # CRÍTICO: Formato Data URL
        return f"data:{mime_type};base64,{base64_string}"
        
    except Exception as e:
        print(f"ERRO CRÍTICO na conversão Base64: {e}")

def login_required(f):
    """Verifica se o usuário está logado."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'danger')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """
    Decorador para verificar se o usuário logado tem permissão de administrador.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Acesso restrito. Faça login.', 'danger')
            return redirect(url_for('login'))

        try:
            # Busca o usuário apenas uma vez no DB para verificar o status
            apostador = usuarios_collection.find_one({'_id': ObjectId(session['usuario_id'])})
        except Exception:
            apostador = None

        if not apostador or not apostador.get('is_admin'):
            flash('Acesso negado. Você não tem permissões de administrador.', 'danger')
            return redirect(url_for('painel') if 'usuario_id' in session else url_for('login'))

        return f(*args, **kwargs)
    return decorated_function


# --- Funções Utilitárias para Templates (Jinja2) ---
@app.context_processor
def utility_processor():
    """Funções que podem ser chamadas diretamente no HTML (Jinja2)."""

    def get_time_sigla(time_id_str):
        """Busca a sigla de um time pelo seu ObjectId (string)."""
        try:
            time = times_collection.find_one({'_id': ObjectId(time_id_str)})
            return time['sigla'] if time else '???'
        except Exception:
            return '???'

    def get_db_rodadas():
        """Retorna todas as rodadas serializadas, ordenadas por número."""
        rodadas = list(rodadas_collection.find().sort('numero', 1))
        return serialize_mongo_object(rodadas)

    def now_date():
        # A função original 'now_date' só retornava a data.
        # Mantida para compatibilidade, mas a lógica de comparação agora usa datetime.now()
        return date.today().strftime('%Y-%m-%d')

    def get_proxima_rodada_aberta():
        """Retorna o objeto da próxima rodada aberta para apostas."""
        # Agora checa data e hora!
        agora = datetime.now()
        agora_str = agora.strftime(DATETIME_FORMAT)

        rodada = rodadas_collection.find_one({
            'data_limite_apostas': {'$gte': agora_str}
        }, sort=[('numero', 1)])
        return serialize_mongo_object(rodada)

    def get_palpite_do_jogo(rodada_id_str, jogo_id_str):
        """Busca o palpite do usuário logado para um jogo específico da rodada."""
        if 'usuario_id' not in session:
            return None
        try:
            palpite_rodada = palpites_collection.find_one({
                'usuario_id': ObjectId(session['usuario_id']),
                'rodada_id': ObjectId(rodada_id_str)
            })

            if palpite_rodada:
                for palpite in palpite_rodada.get('palpites', []):
                    # Compara strings, pois o jogo_id_str vem do template, e id_jogo já foi serializado no DB
                    if str(palpite.get('id_jogo')) == jogo_id_str:
                        # Retorna o dicionário de palpite (placar_casa, placar_visitante)
                        return palpite
            return None
        except Exception:
            return None

    return dict(
        get_db_rodadas=get_db_rodadas,
        get_time_sigla=get_time_sigla,
        now_date=now_date,
        get_proxima_rodada_aberta=get_proxima_rodada_aberta,
        get_palpite_do_jogo=get_palpite_do_jogo
    )


# --- FUNÇÃO CENTRAL DE PONTUAÇÃO ---
def calcular_pontuacao_jogo(placar_oficial_casa, placar_oficial_visitante, palpite_casa, palpite_visitante):
    """Calcula a pontuação de um único jogo com base nas regras simplificadas do bolão."""

    if (placar_oficial_casa is None or placar_oficial_visitante is None or
            palpite_casa is None or palpite_visitante is None):
        return 0

    # 1. Checa Acerto do Placar Exato (10 Pontos)
    acertou_placar_exato = (
        placar_oficial_casa == palpite_casa and
        placar_oficial_visitante == palpite_visitante
    )

    if acertou_placar_exato:
        return 10

    # 2. Checa Acerto do Resultado (Seco)

    # Define o resultado oficial (1: Casa vence, -1: Visitante vence, 0: Empate)
    resultado_oficial = 0
    if placar_oficial_casa > placar_oficial_visitante:
        resultado_oficial = 1
    elif placar_oficial_casa < placar_oficial_visitante:
        resultado_oficial = -1

    # Define o resultado do palpite
    resultado_palpite = 0
    if palpite_casa > palpite_visitante:
        resultado_palpite = 1
    elif palpite_casa < palpite_visitante:
        resultado_palpite = -1

    acertou_resultado = (resultado_oficial == resultado_palpite)

    # 3. Regra: Resultado Seco (5 Pontos)
    if acertou_resultado:
        return 5

    # 4. Errou Tudo (0 Pontos)
    return 0


# --- ROTAS PRINCIPAIS E AUTENTICAÇÃO ---
@app.route('/')
def index():
    if 'usuario' in session:
        return redirect(url_for('painel'))
    return render_template('index.html')


@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    if request.method == 'POST':
        nome = request.form.get('nome')
        usuario = request.form.get('usuario')
        senha = request.form.get('senha')

        if usuarios_collection.find_one({'usuario': usuario}):
            flash('Este nome de usuário já está em uso.', 'danger')
            return render_template('cadastro.html')

        hashed_password = bcrypt.generate_password_hash(senha).decode('utf-8')

        novo_apostador = {
            'nome': nome,
            'usuario': usuario,
            'senha': hashed_password,
            'is_admin': False
        }
        usuarios_collection.insert_one(novo_apostador)

        flash('Cadastro realizado com sucesso! Faça login para continuar.', 'success')
        return redirect(url_for('login'))

    return render_template('cadastro.html')









































@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario_digitado = request.form.get('usuario')
        senha_digitada = request.form.get('senha')

        apostador = usuarios_collection.find_one({'usuario': usuario_digitado})

        if apostador and bcrypt.check_password_hash(apostador['senha'], senha_digitada):
            session['usuario_id'] = str(apostador['_id'])
            session['usuario'] = apostador['usuario']
            session['nome_completo'] = apostador['nome']
            session['is_admin'] = apostador.get('is_admin', False)

            flash(f'Bem-vindo(a) de volta, {apostador["nome"].split()[0]}!', 'success')
            return redirect(url_for('painel'))
        else:
            flash('Usuário ou senha inválidos. Tente novamente.', 'danger')
            return render_template('login.html')

    return render_template('login.html')


@app.route('/painel')
@login_required
def painel():
    # 1. Obter a data e hora atual no formato do seu banco de dados (ex: 'DD/MM/YYYY HH:MM')
    # Assumindo que você usa DATETIME_FORMAT = '%d/%m/%Y %H:%M'
    agora = datetime.now().strftime(DATETIME_FORMAT)
    
    # --- Lógica de Busca de Rodadas ---

    # 2. Busca a próxima rodada aberta
    # CRÍTICO: Se sua data está no formato 'DD/MM/YYYY HH:MM', a comparação de strings
    # do MongoDB ('$gt') pode não funcionar como esperado (não é ordem cronológica).
    # Vamos buscar todas as rodadas e filtrar no Python para maior precisão.
    
    todas_rodadas = list(rodadas_collection.find().sort([('numero', -1)]))
    
    rodada_aberta = None
    rodadas_disponiveis = []

    for rodada in todas_rodadas:
        try:
            # Converte a data do DB (string) para objeto datetime para comparação
            data_limite = datetime.strptime(rodada['data_limite_apostas'], DATETIME_FORMAT)
            
            # Se o prazo ainda não passou
            if data_limite > datetime.now():
                # Esta rodada está disponível para apostas ou consulta
                rodadas_disponiveis.append(rodada)
                
                # A primeira rodada que encontrarmos com prazo futuro (por ser a mais recente ou por ordenação)
                # será a rodada aberta principal
                if rodada_aberta is None:
                    rodada_aberta = rodada
            else:
                # O prazo passou, mas a rodada ainda está disponível para CONSULTA GERAL
                rodadas_disponiveis.append(rodada)

        except (ValueError, TypeError):
            # Ignora rodadas com formato de data inválido
            continue 

    # 3. Serializa os objetos do MongoDB antes de enviar para o Jinja2
    if rodada_aberta:
        rodada_aberta = serialize_mongo_object(rodada_aberta)
    
    rodadas_disponiveis = serialize_mongo_object(rodadas_disponiveis)
    
    # 4. Envia as variáveis para o template
    return render_template('painel.html', 
                           is_admin=session.get('is_admin', False),
                           rodada_aberta=rodada_aberta, 
                           rodadas_disponiveis=rodadas_disponiveis)


@app.route('/logout')
def logout():
    session.clear()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('login'))


# --- ROTAS DE APOSTAS DO USUÁRIO ---
# ASSUMINDO QUE AS FUNÇÕES serialize_mongo_object e get_time_by_id ESTÃO DEFINIDAS EM OUTRO LUGAR
# Se não estiverem, adicione as funções auxiliares que forneci anteriormente.

@app.route('/salvar_aposta/<rodada_id>', methods=['POST'])
@login_required
def salvar_aposta(rodada_id):
    try:
        rodada_object_id = ObjectId(rodada_id)
        rodada = rodadas_collection.find_one({'_id': rodada_object_id})

        if not rodada:
            flash("Rodada não encontrada.", 'danger')
            return redirect(url_for('painel'))

        # --- VERIFICAÇÃO DE DATA E HORA LIMITE ---
        try:
            data_limite_aposta = datetime.strptime(rodada['data_limite_apostas'], DATETIME_FORMAT)
        except (ValueError, TypeError):
            flash("Erro interno: Formato da data limite da rodada inválido.", 'danger')
            return redirect(url_for('painel'))

        agora = datetime.now()
        if agora >= data_limite_aposta:
            data_formatada = data_limite_aposta.strftime('%d/%m/%Y às %H:%M')
            flash(f"O prazo para apostar na Rodada {rodada.get('numero')} encerrou em {data_formatada}.", 'danger')
            return redirect(url_for('painel'))
        # ------------------------------------------

        usuario_object_id = ObjectId(session['usuario_id'])
        palpites = []

        for jogo in rodada['jogos']:
            # Usar sempre o id_jogo como identificador do jogo
            jogo_identificador = str(jogo['id_jogo'])

            campo_casa = f'placar_casa_{jogo_identificador}'
            campo_visitante = f'placar_visitante_{jogo_identificador}'

            placar_casa_str = request.form.get(campo_casa)
            placar_visitante_str = request.form.get(campo_visitante)

            try:
                placar_casa = int(placar_casa_str) if placar_casa_str and placar_casa_str.strip() != '' else 0
                placar_visitante = int(placar_visitante_str) if placar_visitante_str and placar_visitante_str.strip() != '' else 0
            except ValueError:
                flash('Os placares devem ser números inteiros (0 ou mais).', 'danger')
                return redirect(url_for('apostar', rodada_id=rodada_id))

            palpites.append({
                'id_jogo': jogo_identificador,
                'placar_casa': placar_casa,
                'placar_visitante': placar_visitante
            })

        palpites_collection.update_one(
            {
                'usuario_id': usuario_object_id,
                'rodada_id': rodada_object_id
            },
            {
                '$set': {
                    'palpites': palpites,
                    'data_criacao': datetime.now()
                }
            },
            upsert=True
        )

        flash(f'Palpites da Rodada {rodada["numero"]} salvos com sucesso!', 'success')

    except Exception as e:
        flash(f'Erro ao salvar a aposta: {e}', 'danger')

    return redirect(url_for('painel'))


@app.route('/ranking')
@login_required
def ranking():
    """Exibe o ranking geral acumulado de todos os usuários."""

    ranking_geral = list(ranking_collection.aggregate([
        {
            '$group': {
                '_id': '$usuario_id',
                'pontuacao_total': {'$sum': '$pontuacao_total'}
            }
        },
        {'$sort': {'pontuacao_total': -1}},
        {'$limit': 50}
    ]))

    ranking_final = []
    for rank in ranking_geral:
        usuario = usuarios_collection.find_one({'_id': rank['_id']})
        if usuario:
            ranking_final.append({
                'usuario': usuario['nome'].split()[0],
                'pontuacao': rank['pontuacao_total']
            })

    return render_template('ranking.html', ranking_final=ranking_final)

@app.route('/minhas_apostas')
@login_required
def minhas_apostas():
    """Busca todos os palpites do usuário logado para visualização individual."""
    
    usuario_object_id = ObjectId(session['usuario_id'])
    
    # Busca todos os palpites do usuário logado
    palpites_do_usuario = palpites_collection.find(
        {'usuario_id': usuario_object_id}
    ).sort([('data_criacao', -1)]) # Mostra os mais recentes primeiro
    
    palpites_com_dados_completos = []

    for palpite in palpites_do_usuario:
        
        # 1. Busca a rodada para obter os detalhes (nome, jogos, etc.)
        rodada = rodadas_collection.find_one({'_id': palpite['rodada_id']})
        if not rodada: continue # Ignora se a rodada não for encontrada

        # 2. Anexa os dados completos dos times (escudos) a CADA JOGO no palpite
        for p in palpite['palpites']:
            # Localiza o jogo original na rodada usando o 'id_jogo'
            jogo_original = next((j for j in rodada['jogos'] if str(j['id_jogo']) == p['id_jogo']), None)
            
            if jogo_original:
                # Usa a função auxiliar para buscar os dados completos do time
                p['time_casa'] = get_time_by_id(jogo_original['time_casa_id'])
                p['time_visitante'] = get_time_by_id(jogo_original['time_visitante_id'])
        
        # 3. Monta o objeto final para o template
        dados_completos = {
            'rodada_numero': rodada.get('numero'),
            'data_criacao': palpite.get('data_criacao'),
            'data_limite': rodada.get('data_limite_apostas'),
            'palpites': palpite['palpites']
        }
        
        palpites_com_dados_completos.append(dados_completos)

    # Renderiza o novo template
    return render_template('minhas_apostas.html', 
                           palpites_do_usuario=palpites_com_dados_completos)


# --- ROTA DE CONSULTA DE PALPITES (CORRIGIDA) ---
@app.route('/consulta_palpites/<rodada_id>')
@login_required
def consulta_palpites(rodada_id):
    """
    CORRIGIDO: Exibe os palpites de todos os usuários para uma rodada específica,
    só permitindo a visualização após a data E hora limite.
    """
    try:
        rodada_object_id = ObjectId(rodada_id)
        rodada = rodadas_collection.find_one({'_id': rodada_object_id})

        if not rodada:
            flash('Rodada não encontrada.', 'danger')
            return redirect(url_for('painel'))

        # --- VERIFICAÇÃO DE DATA E HORA LIMITE ---
        try:
            data_limite_aposta = datetime.strptime(rodada['data_limite_apostas'], DATETIME_FORMAT)
        except (ValueError, TypeError):
            flash("Erro interno: Formato da data limite da rodada inválido.", 'danger')
            return redirect(url_for('painel'))

        agora = datetime.now()

        # Regra de Segurança: Só permite ver os palpites se o prazo expirou
        if agora < data_limite_aposta:
            data_formatada = data_limite_aposta.strftime('%d/%m/%Y às %H:%M')
            flash(f'A consulta de palpites para a Rodada {rodada["numero"]} só estará disponível após {data_formatada}.', 'warning')
            return redirect(url_for('painel'))
        # ------------------------------------------

        # 1. Busca todos os palpites para esta rodada
        palpites = list(palpites_collection.find({'rodada_id': rodada_object_id}))

        # 2. Mapeia os dados: associa o palpite ao nome do usuário
        palpites_mapeados = []
        for palpite in palpites:
            usuario = usuarios_collection.find_one({'_id': palpite['usuario_id']})
            # Garante que o usuário existe e não é o administrador
            if usuario and not usuario.get('is_admin'):
                palpites_mapeados.append({
                    'apostador_nome': usuario['nome'].split()[0],
                    'palpites': serialize_mongo_object(palpite.get('palpites', []))  # Serializa os palpites internos
                })

        # Ordena a lista de apostadores por nome
        palpites_mapeados.sort(key=lambda x: x['apostador_nome'])

        serializable_rodada = serialize_mongo_object(rodada)

        return render_template('consulta_palpites.html',
                               rodada=serializable_rodada,
                               palpites_mapeados=palpites_mapeados)

    except Exception as e:
        flash(f'Erro ao carregar a consulta de palpites: {e}', 'danger')
        return redirect(url_for('painel'))


# --- ROTAS DE ADMINISTRAÇÃO ---
@app.route('/admin')
@admin_required
def admin_index():
    return redirect(url_for('admin_times'))


@app.route('/admin/times')
@admin_required
def admin_times():
    times = list(times_collection.find().sort('nome', 1))
    serializable_times = serialize_mongo_object(times)
    return render_template('admin_times.html', times=serializable_times)


@app.route('/admin/rodadas')
@admin_required
def admin_rodadas():
    times = list(times_collection.find().sort('nome', 1))
    serializable_times = serialize_mongo_object(times)

    rodadas = list(rodadas_collection.find().sort('numero', -1))  # Mais recente primeiro
    serializable_rodadas = serialize_mongo_object(rodadas)

    return render_template('admin_rodadas.html', times=serializable_times, rodadas=serializable_rodadas)


# --- ROTA DE STATUS DE APOSTAS ---
@app.route('/admin/status_apostas')
@admin_required
def admin_status_apostas():
    """
    Mostra a lista de todos os usuários e o status de aposta (Sim/Não)
    para a próxima rodada aberta, excluindo o administrador.
    """
    # Agora checa data e hora!
    agora = datetime.now()
    agora_str = agora.strftime(DATETIME_FORMAT)

    # 1. Encontra a próxima rodada aberta para apostas
    rodada_aberta = rodadas_collection.find_one({
        'data_limite_apostas': {'$gte': agora_str}
    }, sort=[('numero', 1)])

    if not rodada_aberta:
        flash('Nenhuma rodada aberta no momento para verificar o status de apostas.', 'warning')
        return redirect(url_for('admin_index'))

    rodada_id = rodada_aberta['_id']

    # 2. Busca TODOS os usuários que não são admin
    todos_usuarios = list(usuarios_collection.find({'is_admin': {'$ne': True}}))

    # 3. Processa e verifica o status de aposta
    lista_status = []
    for usuario in todos_usuarios:
        palpite_existe = palpites_collection.find_one({
            'usuario_id': usuario['_id'],
            'rodada_id': rodada_id
        })

        lista_status.append({
            'nome': usuario.get('nome', 'N/A'),
            'usuario': usuario.get('usuario', 'N/A'),
            'apostou': True if palpite_existe else False
        })

    # Ordena a lista de status: não apostou primeiro (False < True)
    lista_status.sort(key=lambda x: (x['apostou'], x['nome']))

    return render_template('admin_apostas_status.html',
                           lista_status=lista_status,
                           rodada_numero=rodada_aberta['numero'])


# --- AÇÕES CRUD DE TIMES ---
@app.route('/admin/times/cadastrar', methods=['POST'])
@admin_required
def cadastrar_time():
    nome = request.form.get('nome').strip()
    sigla = request.form.get('sigla').strip().upper()
    
    # NOVO CAMPO: Pega o objeto de upload do arquivo
    escudo_file = request.files.get('escudo_file') 
    
    # Converte o arquivo para Base64 (Retorna None se não houver arquivo)
    escudo_base64 = image_to_base64(escudo_file) 

    if not nome or not sigla or len(sigla) != 3:
        flash('Nome e Sigla (3 letras) são obrigatórios.', 'danger')
    elif times_collection.find_one({'$or': [{'nome': nome}, {'sigla': sigla}]}):
        flash('Time ou sigla já cadastrados.', 'danger')
    else:
        novo_time = {
            'nome': nome,
            'sigla': sigla,
            'escudo_base64': escudo_base64 # <--- SALVA A STRING BASE64
        }
        times_collection.insert_one(novo_time)
        flash(f'Time "{nome}" cadastrado com sucesso!', 'success')

    return redirect(url_for('admin_times'))




@app.route('/admin/times/editar/<time_id>', methods=['GET'])
@admin_required
def editar_time(time_id):
    try:
        # Tenta converter para ObjectId. 
        time_object_id = ObjectId(time_id) 
        time = times_collection.find_one({'_id': time_object_id})

        if not time:
            flash('Time não encontrado. O time pode ter sido excluído recentemente.', 'danger')
            # Redirecionamento 1 CORRIGIDO!
            return redirect(url_for('admin_times')) 

        serializable_time = serialize_mongo_object(time)
        return render_template('admin_editar_time.html', time=serializable_time)

    # Captura a exceção específica para ID inválido
    except InvalidId:
        flash(f'Erro ao carregar time para edição: ID inválido. (Verifique se a string de ID possui 24 caracteres hexadecimais).', 'danger')
        # Redirecionamento 2 CORRIGIDO!
        return redirect(url_for('admin_times')) 

    # Captura qualquer outro erro.
    except Exception as e:
        # Esta exceção foi atingida devido à falha de 'admin_painel'.
        flash(f'Ocorreu um erro desconhecido ao carregar o time: {e}', 'danger')
        # Redirecionamento 3 CORRIGIDO!
        return redirect(url_for('admin_times'))

@app.route('/admin/times/atualizar/<time_id>', methods=['POST'])
@admin_required
def atualizar_time(time_id):
    nome_novo = request.form.get('nome').strip()
    sigla_nova = request.form.get('sigla').strip().upper()
    
    # --- NOVO TRECHO CRÍTICO ---
    escudo_file = request.files.get('escudo_file')
    time_object_id = ObjectId(time_id)
    
    # 1. Busca o time atual para pegar o Base64 existente
    time_atual = times_collection.find_one({'_id': time_object_id})
    escudo_base64_novo = time_atual.get('escudo_base64') # Assume o valor antigo
    
    # 2. Se um NOVO ARQUIVO foi enviado, sobrescreve o Base64
    if escudo_file and escudo_file.filename:
        escudo_base64_novo = image_to_base64(escudo_file)
    # --- FIM DO TRECHO CRÍTICO ---

    if not nome_novo or not sigla_nova or len(sigla_nova) != 3:
        flash('Nome e Sigla (3 letras) são obrigatórios.', 'danger')
        return redirect(url_for('editar_time', time_id=time_id))

    try:
        # ... (sua lógica de verificação de time duplicado) ...
        
        # 3. ATUALIZAÇÃO NO BANCO
        resultado = times_collection.update_one(
            {'_id': time_object_id},
            {'$set': {
                'nome': nome_novo, 
                'sigla': sigla_nova,
                'escudo_base64': escudo_base64_novo # <--- CAMPO SALVO
            }}
        )

        # ... (resto da lógica de flash) ...

    except Exception as e:
        flash(f'Erro ao processar a atualização: {e}', 'danger')

    return redirect(url_for('admin_times'))


@app.route('/admin/times/excluir/<time_id>', methods=['POST'])
@admin_required
def excluir_time(time_id):
    try:
        time_object_id = ObjectId(time_id)

        if rodadas_collection.find_one({'$or': [{'jogos.time_casa_id': time_object_id}, {'jogos.time_visitante_id': time_object_id}]}):
            flash('Não é possível excluir este time. Ele já está escalado em uma rodada cadastrada.', 'danger')
            return redirect(url_for('admin_times'))

        resultado = times_collection.delete_one({'_id': time_object_id})

        if resultado.deleted_count == 1:
            flash('Time excluído com sucesso!', 'success')
        else:
            flash('Erro ao excluir time: Time não encontrado.', 'danger')

    except Exception:
        flash(f'Erro ao excluir time: ID inválido.', 'danger')

    return redirect(url_for('admin_times'))


# --- AÇÕES CRUD DE RODADAS ---
# --- AÇÕES CRUD DE RODADAS ---

@app.route('/admin/rodadas/cadastrar', methods=['POST'])
@admin_required
def cadastrar_rodada():
    """
    CORRIGIDO: Recebe Data e Hora em campos separados, combina em uma única string 
    no formato YYYY-MM-DDTHH:MM e salva no DB.
    """
    try:
        numero_rodada = int(request.form.get('numero_rodada'))
        # ALTERAÇÃO CRÍTICA: Recebe a data e a hora separadamente
        data_limite = request.form.get('data_limite') 
        hora_limite = request.form.get('hora_limite')

        if rodadas_collection.find_one({'numero': numero_rodada}):
            flash(f'A Rodada {numero_rodada} já está cadastrada.', 'danger')
            return redirect(url_for('admin_rodadas'))
        
        # Validação simples
        if not data_limite or not hora_limite:
            flash('O campo de Data e Hora Limite é obrigatório.', 'danger')
            return redirect(url_for('admin_rodadas'))
            
        # COMBINA A DATA E HORA NO FORMATO SALVO PELO datetime-local (YYYY-MM-DDTHH:MM)
        data_hora_limite_combinada = f"{data_limite}T{hora_limite}"
        # Tenta validar o formato
        try:
             datetime.strptime(data_hora_limite_combinada, DATETIME_FORMAT)
        except ValueError:
            flash('Erro de formato: Data ou Hora inválidas.', 'danger')
            return redirect(url_for('admin_rodadas'))


        times_casa_ids = request.form.getlist('time_casa_id')
        times_visitante_ids = request.form.getlist('time_visitante_id')
        
        jogos = []
        
        for i in range(len(times_casa_ids)):
            casa_id_str = times_casa_ids[i]
            visitante_id_str = times_visitante_ids[i]

            if not casa_id_str and not visitante_id_str:
                continue
            
            if not casa_id_str or not visitante_id_str:
                flash('Erro: Um jogo foi preenchido de forma incompleta (faltou o Time Casa ou o Time Visitante). Corrija o formulário.', 'danger')
                return redirect(url_for('admin_rodadas'))
            
            casa_id = ObjectId(casa_id_str)
            visitante_id = ObjectId(visitante_id_str)

            jogos.append({
                'id_jogo': ObjectId(), 
                'time_casa_id': casa_id,
                'time_visitante_id': visitante_id,
                'placar_casa': None, 
                'placar_visitante': None,
                'finalizado': False
            })

        if not jogos:
            flash('Erro: Nenhuma partida válida foi definida para esta rodada.', 'danger')
            return redirect(url_for('admin_rodadas'))


        nova_rodada = {
            'numero': numero_rodada,
            # ALTERAÇÃO CRÍTICA: Salva a string combinada
            'data_limite_apostas': data_hora_limite_combinada, 
            'jogos': jogos,
            'processada': False 
        }
        
        rodadas_collection.insert_one(nova_rodada)
        flash(f'Rodada {numero_rodada} cadastrada com sucesso com {len(jogos)} jogos!', 'success')

    except ValueError:
        flash('Erro de formato. Verifique se o número da rodada é um número inteiro ou se um ID de time é inválido.', 'danger')
    except Exception as e:
        flash(f'Ocorreu um erro ao cadastrar a rodada: {e}', 'danger')

    return redirect(url_for('admin_rodadas'))

@app.route('/admin/rodadas/excluir/<rodada_id>', methods=['POST'])
@admin_required
def excluir_rodada(rodada_id):
    try:
        rodada_object_id = ObjectId(rodada_id)

        resultado = rodadas_collection.delete_one({'_id': rodada_object_id})

        if resultado.deleted_count == 1:
            # Também deleta palpites e rankings relacionados a esta rodada
            palpites_collection.delete_many({'rodada_id': rodada_object_id})
            ranking_collection.delete_many({'rodada_id': rodada_object_id})
            flash('Rodada, palpites e rankings relacionados excluídos com sucesso!', 'success')
        else:
            flash('Erro ao excluir rodada: Rodada não encontrada.', 'danger')

    except Exception:
        flash(f'Erro ao excluir rodada: ID inválido.', 'danger')

    return redirect(url_for('admin_rodadas'))


# --- ROTAS DE REGISTRO DE PLACAR (CORRIGIDAS) ---
@app.route('/admin/placar')
@admin_required
def placar_admin_lista():
    """Busca e lista todas as rodadas para registro de placar."""
    # Busca todas as rodadas cadastradas (ordenando da mais recente para a mais antiga)
    rodadas = list(rodadas_collection.find().sort('numero', -1))

    # Serializa os dados para o Jinja
    serializable_rodadas = serialize_mongo_object(rodadas)

    # Passa a lista de rodadas para o template
    return render_template('admin_placar_lista.html', rodadas=serializable_rodadas)


@app.route('/admin/placar/editar/<rodada_id>', methods=['GET'])
@admin_required
def placar_admin_editar(rodada_id):
    try:
        rodada_object_id = ObjectId(rodada_id)
        rodada = rodadas_collection.find_one({'_id': rodada_object_id})

        if not rodada:
            flash('Rodada não encontrada.', 'danger')
            return redirect(url_for('placar_admin_lista'))

        serializable_rodada = serialize_mongo_object(rodada)

        return render_template('admin_registrar_placar.html', rodada=serializable_rodada)

    except Exception:
        flash(f'Erro ao carregar rodada para placar: ID inválido.', 'danger')
        return redirect(url_for('placar_admin_lista'))


@app.route('/admin/placar/salvar/<rodada_id>', methods=['POST'])
@admin_required
def placar_admin_salvar(rodada_id):
    try:
        rodada_object_id = ObjectId(rodada_id)
        rodada_atual = rodadas_collection.find_one({'_id': rodada_object_id})

        if not rodada_atual:
            flash('Rodada não encontrada para salvar o placar.', 'danger')
            return redirect(url_for('placar_admin_lista'))

        jogos_atualizados = []
        placares_recebidos = 0

        for jogo in rodada_atual['jogos']:
            jogo_id = str(jogo['id_jogo'])

            placar_casa_str = request.form.get(f'placar_casa_{jogo_id}')
            placar_visitante_str = request.form.get(f'placar_visitante_{jogo_id}')

            try:
                placar_casa = int(placar_casa_str) if placar_casa_str is not None and placar_casa_str.strip() != '' else None
                placar_visitante = int(placar_visitante_str) if placar_visitante_str is not None and placar_visitante_str.strip() != '' else None

                jogo['placar_casa'] = placar_casa
                jogo['placar_visitante'] = placar_visitante

                if placar_casa is not None and placar_visitante is not None:
                    jogo['finalizado'] = True
                    placares_recebidos += 1
                else:
                    jogo['finalizado'] = False

                jogos_atualizados.append(jogo)

            except ValueError:
                flash(f'Placares do jogo {jogo_id} não são números válidos e foram ignorados. Os placares anteriores foram mantidos.', 'warning')
                jogos_atualizados.append(jogo)

        rodadas_collection.update_one(
            {'_id': rodada_object_id},
            {'$set': {'jogos': jogos_atualizados}}
        )

        flash(f'Placares da Rodada {rodada_atual["numero"]} ({placares_recebidos}/{len(rodada_atual["jogos"])} jogos) atualizados com sucesso!', 'success')

    except Exception as e:
        flash(f'Erro ao salvar placares: {e}', 'danger')

    return redirect(url_for('placar_admin_editar', rodada_id=rodada_id))


# --- ROTAS DE CÁLCULO E RANKING ---
@app.route('/admin/calcular_ranking/<rodada_id>', methods=['POST'])
@admin_required
def calcular_ranking(rodada_id):
    """Calcula a pontuação de todos os usuários para uma rodada e salva no ranking."""
    try:
        rodada_object_id = ObjectId(rodada_id)
        rodada = rodadas_collection.find_one({'_id': rodada_object_id})

        if not rodada:
            flash('Rodada não encontrada.', 'danger')
            return redirect(url_for('admin_index'))

        jogos_nao_finalizados = [j for j in rodada['jogos'] if j.get('finalizado') == False]
        if jogos_nao_finalizados:
            flash(f'A Rodada {rodada["numero"]} não pode ser pontuada. Pelo menos um jogo ainda não tem placar oficial.', 'warning')
            return redirect(url_for('placar_admin_lista'))

        if rodada.get('processada', False):
            flash(f'A Rodada {rodada["numero"]} já foi processada. Desfaça a pontuação antes de tentar novamente.', 'warning')
            return redirect(url_for('placar_admin_lista'))

        palpites = list(palpites_collection.find({'rodada_id': rodada_object_id}))

        if not palpites:
            flash(f'Nenhum palpite encontrado para a Rodada {rodada["numero"]}.', 'info')
            return redirect(url_for('placar_admin_lista'))

        for palpite in palpites:
            pontuacao_total = 0

            for palpite_jogo in palpite.get('palpites', []):
                jogo_id = palpite_jogo['id_jogo']

                jogo_oficial = next((j for j in rodada['jogos'] if str(j['id_jogo']) == str(jogo_id)), None)  # Compara string-string

                if jogo_oficial:
                    pontos = calcular_pontuacao_jogo(
                        jogo_oficial.get('placar_casa'),
                        jogo_oficial.get('placar_visitante'),
                        palpite_jogo.get('placar_casa'),
                        palpite_jogo.get('placar_visitante')
                    )
                    pontuacao_total += pontos

            ranking_collection.update_one(
                {
                    'usuario_id': palpite['usuario_id'],
                    'rodada_id': rodada_object_id
                },
                {
                    '$set': {
                        'pontuacao_total': pontuacao_total,
                        'data_calculo': datetime.now()
                    }
                },
                upsert=True
            )

        rodadas_collection.update_one(
            {'_id': rodada_object_id},
            {'$set': {'processada': True}}
        )

        flash(f'Cálculo de pontuação da Rodada {rodada["numero"]} concluído com sucesso! {len(palpites)} apostadores processados.', 'success')

    except Exception as e:
        flash(f'Erro fatal ao calcular o ranking: {e}', 'danger')

    return redirect(url_for('placar_admin_lista'))


# --- Execução do Aplicativo ---
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
