# -*- coding: utf-8 -*-
"""
Contador de Almas - overlay para Aion 2.

Recebe passivamente pelo Npcap os eventos que o jogo envia ao proprio PC e
atualiza os contadores de almas, Pontos do Abismo e Kinah.

NAO le a memoria do jogo, NAO envia pacotes e NAO pede login.
"""
import asyncio
import ctypes
import difflib
import hashlib
import json
import os
import queue
import re
import sys
import threading
import time
import unicodedata
from collections import Counter, deque

# --------------------------------------------------------------------------
# Caminhos e configuracao
# --------------------------------------------------------------------------
APP_NOME = "Contador de Almas"
VERSAO = "1.9.88"
REPO_RAW = "https://raw.githubusercontent.com/vitoriaAngelo/contadorAion2/main/"
# arquivos que a atualizacao troca (os que nao existirem na pasta sao ignorados,
# menos o programa, que e obrigatorio)
ARQUIVOS_ATUALIZAR = ["contador_almas.py", "icone.ico", "desinstalar.ps1", "requirements.txt",
                     "pet_protocol.py", "pet_nomes_npcap.json",
                     "LICENSE-PET-PROTOCOL.txt"]


def versao_tupla(v):
    n = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(n + [0] * (3 - len(n)))


def assinatura_estado_pets(levels, points):
    """Identifica a coleção do personagem sem guardar os dados no config."""
    dados = [sorted((int(k), int(v)) for k, v in levels.items()),
             sorted((int(k), int(v)) for k, v in points.items())]
    bruto = json.dumps(dados, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(bruto).hexdigest()[:20]


def baixar_texto(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "ContadorDeAlmas/" + VERSAO,
                                               "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def versao_remota():
    """Le a VERSAO do contador_almas.py que esta no GitHub. (versao, bytes)"""
    dados = baixar_texto(REPO_RAW + "contador_almas.py")
    m = re.search(rb'^VERSAO\s*=\s*"([^"]+)"', dados, re.M)
    if not m:
        raise RuntimeError("n\u00e3o achei a vers\u00e3o no arquivo do GitHub")
    return m.group(1).decode(), dados


def aplicar_atualizacao(dados_py, pasta):
    """Troca os arquivos do programa pela versao nova. Valida antes (o arquivo
    precisa compilar) e guarda a versao anterior em contador_almas.py.bak."""
    compile(dados_py, "contador_almas.py", "exec")  # erro aqui = nao troca nada
    extras = {}
    for nome in ARQUIVOS_ATUALIZAR[1:]:
        if os.path.exists(os.path.join(pasta, nome)) or nome in (
                "pet_protocol.py", "pet_nomes_npcap.json",
                "LICENSE-PET-PROTOCOL.txt"):
            try:
                extras[nome] = baixar_texto(REPO_RAW + nome)
            except Exception:
                if nome in ("pet_protocol.py", "pet_nomes_npcap.json",
                            "LICENSE-PET-PROTOCOL.txt"):
                    raise
    alvo = os.path.join(pasta, "contador_almas.py")
    if os.path.exists(alvo):
        with open(alvo, "rb") as f:
            antigo = f.read()
        with open(alvo + ".bak", "wb") as f:
            f.write(antigo)
    req_antes = b""
    cam_req = os.path.join(pasta, "requirements.txt")
    if os.path.exists(cam_req):
        with open(cam_req, "rb") as f:
            req_antes = f.read()
    for nome, dados in [("contador_almas.py", dados_py)] + list(extras.items()):
        tmp = os.path.join(pasta, nome + ".novo")
        with open(tmp, "wb") as f:
            f.write(dados)
        os.replace(tmp, os.path.join(pasta, nome))
    # O contador roda elevado para abrir o Npcap. Nesse modo o Python pode não
    # enxergar pacotes instalados com --user; mantenha as dependências junto ao app.
    return (extras.get("requirements.txt", req_antes) != req_antes or
            not os.path.isdir(os.path.join(pasta, "psutil")))


def esperar_processo(pid, limite=15.0):
    """A versao nova espera a antiga fechar antes de abrir a janela."""
    fim = time.time() + limite
    if sys.platform == "win32":
        try:
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
            if h:
                k.WaitForSingleObject(h, int(limite * 1000))
                k.CloseHandle(h)
            return
        except Exception:
            pass
    while time.time() < fim:
        try:
            os.kill(int(pid), 0)
        except Exception:
            return
        time.sleep(0.2)


def pasta_base():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = pasta_base()
ARQ_CONFIG = os.path.join(BASE, "config.json")
ARQ_DADOS = os.path.join(BASE, "progresso.json")

# Padroes (expressoes regulares) que reconhecem a mensagem de alma.
# O grupo (?P<nome>...) e o nome do monstro. Edite pelo menu "Ver leitura".
PADROES_PADRAO = [
    # Aion 2 (PT-BR): "Obteve Pontos da Alma Zaif Mutante x1."
    r"pontos\s+d[ae]\s+alma\s+(?P<nome>.+?)\s*[x×]\s*\d",
    r"pontos\s+d[ae]\s+alma\s+(?P<nome>[^\[\]\(\)\{\}:|.]+)",
    r"alma\s+d[eoa]s?\s+(?P<nome>[^\[\]\(\)\{\}:|]+)",
    r"soul\s+of\s+(?P<nome>[^\[\]\(\)\{\}:|]+)",
    r"(?P<nome>[^\[\]\(\)\{\}:|]+?)\s+soul\b",
]

CONFIG_PADRAO = {
    "meta": 105,
    "tiers": [5, 25, 75],
    # proximo evento do jogo, no horario de Brasilia do servidor SA
    # (agenda de referencia: https://www.animoria.com.br/#agenda).
    # "horas": null = toda hora. "dias": 0=segunda ... 6=domingo (opcional).
    "eventos": [
        {"nome": "Shugo", "minuto": 0, "horas": None, "duracao": 10},
        # Mercador Nuvem Negra: a loja renova toda hora cheia
        {"nome": "Loja", "agora": "Loja nova", "minuto": 0, "horas": None, "duracao": 5},
        {"nome": "Invas\u00e3o", "minuto": 30, "horas": None, "duracao": 3},
        {"nome": "Fenda", "minuto": 0,
         "horas": [2, 5, 8, 11, 14, 17, 20, 23], "duracao": 10},
        {"nome": "Kairah", "minuto": 0,
         "horas": [1, 4, 7, 10, 13, 16, 19, 22], "duracao": 30},
        {"nome": "Nahma", "minuto": 0, "horas": [21], "dias": [4, 6], "duracao": 30},
    ],      # almas para cada estrela (somam a meta: 5+25+75=105)
    "regiao": None,            # {"left","top","width","height"} em pixels reais
    "fonte_leitura": "npcap",  # eventos de alma recebidos do jogo pela rede
    "intervalo": 0.3,          # segundos entre capturas
    "motor_ocr": "auto",       # auto | windows | tesseract
    "idioma": "pt-BR",
    "escala_ocr": 2.0,         # amplia a imagem antes do OCR (ajuda em fonte pequena)
    "leituras_paralelas": 2,   # quantas leituras do Tesseract ao mesmo tempo
    "esconder_da_captura": False,  # False = overlay aparece no OBS/Discord
    "filtro_contorno": True,   # isola letras com contorno escuro (cenario claro atras)
    "limiar_claro": 150,
    "limiar_escuro": 90,
    "padroes": PADROES_PADRAO,
    "opacidade": 0.9,
    "posicao": [40, 200],
    "max_linhas": 6,  # acima disso a lista rola
    "aviso_som": True,  # som 1 minuto antes dos eventos
    "volume_aviso": 0.5,  # 0.25 baixo / 0.5 medio / 0.75 alto / 1.0 maximo
    "compacto": False,  # uma linha por bicho
    "tamanho_letra": "normal",  # pequena / normal / grande / muito grande
    "mostrar_concluidos": False,
    "recolhido": False,
}


ARQ_LOG = os.path.join(BASE, "diagnostico.log")


def registrar(texto):
    try:
        if os.path.exists(ARQ_LOG) and os.path.getsize(ARQ_LOG) > 300_000:
            os.replace(ARQ_LOG, ARQ_LOG + ".old")
        with open(ARQ_LOG, "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + texto + "\n")
    except Exception:
        pass


def estrelas(qtd, tiers):
    """Devolve (estrelas_completas, quanto_falta_para_a_proxima, total_estrelas).
    Os tiers se somam: [5, 25, 75] -> 1 estrela com 5, 2 com 30, 3 com 105."""
    acum, feitas = 0, 0
    for t in tiers:
        acum += t
        if qtd >= acum:
            feitas += 1
        else:
            return feitas, acum - qtd, len(tiers)
    return feitas, 0, len(tiers)


def _hex(c):
    """'#rrggbb' -> (r, g, b)"""
    c = c.lstrip("#"); return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def linhas_radar(mobs, posicao, catalogo, especies, agora):
    """Monstros vistos na zona atual que dão almas ainda necessárias."""
    import math
    pets = {r[0]: r for r in catalogo}
    recente = posicao is not None and agora - posicao[2] <= 10
    direcoes = ("N", "NE", "L", "SE", "S", "SO", "O", "NO")
    linhas = []
    for npc, x, y, visto in mobs.values():
        if agora - visto > 120:
            continue
        especie = especies.get(str(npc))
        if not especie:
            continue
        pet_id, monstro = especie
        pet = pets.get(str(pet_id))
        if not pet or not str(pet[3]).isdigit() or int(pet[3]) <= 0:
            continue
        distancia = direcao = None
        if recente:
            dx, dy = x - posicao[0], y - posicao[1]
            distancia = math.hypot(dx, dy) / 100
            if distancia > 150:
                continue
            direcao = direcoes[round(math.degrees(math.atan2(dx, -dy)) / 45) % 8]
        linhas.append((monstro, pet[1], int(pet[3]), distancia, direcao, visto))
    linhas.sort(key=lambda r: (r[3] is None, r[3] if r[3] is not None else -r[5], r[0]))
    return linhas


RADAR_MAPAS = {
    "verteron": ("Verteron", (1010, 902), .019583509634873143, 8218.937975984913, 8147.450753846877, .25),
    "altgard": ("Altgard", (1110, 901), .0098227439264612, 4137.781824319782, 4062.9907075320293, .5),
    "abyss": ("Reshanta", (), .009878286530741168, 2047.1930445744983, 2056.9061147139305, 1.0),
}
RADAR_RAIO_EXATO = 80
RADAR_RAIO_ESTIMADO = 140


def centro_radar_anunciado(mobs, anterior, agora):
    """Centro estável dos bichos recebidos; é referência de área, não do jogador."""
    atuais = [(m[1], m[2]) for m in mobs.values() if agora - m[3] < 60]
    if not atuais:
        return anterior
    x = sum(p[0] for p in atuais) / len(atuais)
    y = sum(p[1] for p in atuais) / len(atuais)
    if anterior and agora - anterior[2] < 30:
        x = anterior[0] * .55 + x * .45
        y = anterior[1] * .55 + y * .45
    return x, y, agora


def avancar_centro_radar(atual, destino, intervalo):
    """Desloca a câmera sem saltos; mudanças grandes de região são imediatas."""
    import math
    if atual is None or destino is None:
        return destino
    dx, dy = destino[0] - atual[0], destino[1] - atual[1]
    distancia = math.hypot(dx, dy)
    if distancia > 30000 or distancia < 30:
        return destino[:2]
    fator = 1 - math.exp(-max(0, min(intervalo, .2)) / .23)
    return atual[0] + dx * fator, atual[1] + dy * fator


def locais_radar(pontos, posicao, catalogo, especies, raio=200):
    """Pontos de farm conhecidos perto da última posição, sem afirmar que há mob vivo."""
    import math
    if posicao is None:
        return []
    pets = {r[0]: r for r in catalogo}
    x0, y0 = posicao[:2]
    r_world = raio * 100
    direcoes = ("N", "NE", "L", "SE", "S", "SO", "O", "NO")
    linhas = []
    for npc, coordenadas in pontos.items():
        especie = especies.get(str(npc))
        if not especie:
            continue
        pet = pets.get(str(especie[0]))
        if not pet or not str(pet[3]).isdigit() or int(pet[3]) <= 0:
            continue
        for x, y in coordenadas:
            dx, dy = x - x0, y - y0
            if abs(dx) > r_world or abs(dy) > r_world:
                continue
            distancia = math.hypot(dx, dy) / 100
            if distancia > raio:
                continue
            direcao = direcoes[round(math.degrees(math.atan2(dx, -dy)) / 45) % 8]
            linhas.append((especie[1], pet[1], int(pet[3]), distancia,
                           direcao, x, y, str(especie[0])))
    linhas.sort(key=lambda r: r[3])
    return linhas

def imagem_estrela(estado, frac, S, bg, ouro="#f4c46a"):
    """Estrela arredondada e suave (desenhada com o Pillow).
    estado: 'cheia' | 'atual' | 'vazia'. frac: 0..1 (enchimento da atual)."""
    import math
    from PIL import Image, ImageDraw, ImageFilter, ImageChops
    K = 6                      # desenha grande e reduz (borda lisa)
    N = S * K
    cx, cy = N / 2, N / 2 + N * 0.04
    R, r = N * 0.48, N * 0.48 * 0.50
    pts = []
    for i in range(10):
        a = math.radians(-90 + i * 36)
        rr = R if i % 2 == 0 else r
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    m = Image.new("L", (N, N), 0)
    ImageDraw.Draw(m).polygon(pts, fill=255)
    # pontas arredondadas: desfoca e corta
    m = m.filter(ImageFilter.GaussianBlur(N * 0.055)).point(lambda v: 255 if v > 140 else 0)
    borda = ImageChops.subtract(m, m.filter(ImageFilter.MinFilter(int(N * 0.09) | 1)))
    fundo = Image.new("RGBA", (N, N), _hex(bg) + (255,))
    o = _hex(ouro)
    if estado == "cheia":
        # dourado quentinho: claro em cima, mais quente embaixo + brilho
        grad = Image.new("RGBA", (N, N))
        topo, base = (255, 226, 150), (238, 160, 70)
        for y in range(N):
            t = y / N
            cor = tuple(int(topo[k] + (base[k] - topo[k]) * t) for k in range(3))
            ImageDraw.Draw(grad).line([(0, y), (N, y)], fill=cor + (255,))
        fundo.paste(grad, (0, 0), m)
        brilho = Image.new("L", (N, N), 0)
        ImageDraw.Draw(brilho).ellipse((cx - N * 0.16, cy - N * 0.22, cx + N * 0.02, cy - N * 0.06), fill=150)
        brilho = ImageChops.multiply(brilho.filter(ImageFilter.GaussianBlur(N * 0.04)), m)
        fundo.paste((255, 250, 225, 255), (0, 0), brilho)
    elif estado == "atual":
        # miolo enchendo de baixo para cima + contorno dourado
        miolo = Image.new("L", (N, N), 0)
        topo_fill = int(N * (1 - frac))
        ImageDraw.Draw(miolo).rectangle((0, topo_fill, N, N), fill=255)
        miolo = ImageChops.multiply(miolo, m)
        fundo.paste(tuple(int(o[k] * 0.55 + _hex(bg)[k] * 0.45) for k in range(3)) + (255,), (0, 0), miolo)
        fundo.paste(o + (255,), (0, 0), borda)
    else:
        fundo.paste(tuple(int(_hex(bg)[k] + 14) for k in range(3)) + (255,), (0, 0), m)
        fundo.paste((88, 93, 108, 255), (0, 0), borda)
    return fundo.resize((S, S), Image.LANCZOS)


def chave_dia(agora):
    """O dia do jogo vira as 04:00 (reset diario)."""
    import datetime as dt
    return (agora - dt.timedelta(hours=4)).date().isoformat()


def proximo_reset_diario(agora):
    import datetime as dt
    t = agora.replace(hour=4, minute=0, second=0, microsecond=0)
    return t if agora < t else t + dt.timedelta(days=1)


def proximo_reset_semanal(agora):
    """Quarta-feira as 04:00."""
    import datetime as dt
    t = agora.replace(hour=4, minute=0, second=0, microsecond=0)
    t += dt.timedelta(days=(2 - t.weekday()) % 7)
    return t if agora < t else t + dt.timedelta(days=7)


def inicio_semana(agora):
    import datetime as dt
    return proximo_reset_semanal(agora) - dt.timedelta(days=7)


def formatar_longo(seg):
    seg = max(0, int(seg))
    d, r = divmod(seg, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return (f"{d}d " if d else "") + f"{h:02d}:{m:02d}:{s:02d}"


def proximo_inicio(eventos, agora=None):
    """Proximo evento que AINDA NAO comecou (mesmo que outro esteja rolando):
    (nomes, inicio) ou None. Usado para o aviso sonoro."""
    import datetime as dt
    agora = agora or dt.datetime.now()
    base = agora.replace(second=0, microsecond=0)
    melhor, nomes = None, []
    for ev in eventos or []:
        try:
            minuto = int(ev.get("minuto", 0))
            horas, dias = ev.get("horas"), ev.get("dias")
            nome = str(ev.get("nome", "Evento"))
        except Exception:
            continue
        for passo in range(0, 60 * 24 * 8):
            t = base + dt.timedelta(minutes=passo)
            if t <= agora or t.minute != minuto:
                continue
            if horas is not None and t.hour not in horas:
                continue
            if dias is not None and t.weekday() not in dias:
                continue
            if melhor is None or t < melhor:
                melhor, nomes = t, [nome]
            elif t == melhor and nome not in nomes:
                nomes.append(nome)
            break
    return (" + ".join(nomes), melhor) if melhor else None


def criar_som_aviso(caminho, volume=0.5):
    """Gera um 'plim' curto de dois tons (sino suave) em WAV.
    volume de 0.02 a 1.0 (0.5 = o som original)."""
    import math, struct, wave
    taxa = 22050
    amostras = []
    total = int(taxa * 0.95)
    for i in range(total):
        t = i / taxa
        v = 0.0
        for freq, ini, dur in ((880.0, 0.0, 0.55), (1318.5, 0.16, 0.75)):
            if ini <= t < ini + dur:
                x = t - ini
                env = min(1.0, x / 0.008) * math.exp(-x * 5.5)
                v += env * (math.sin(2 * math.pi * freq * x) + 0.25 * math.sin(4 * math.pi * freq * x))
        amostras.append(int(max(-1.0, min(1.0, v * 0.70 * volume)) * 32767))
    with wave.open(caminho, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(struct.pack("<%dh" % len(amostras), *amostras))


VOLUME_AVISO = {"v": 0.5}


def tocar_aviso(volume=None):
    """Toca o aviso sem travar o programa. Fora do Windows so registra."""
    try:
        vol = VOLUME_AVISO["v"] if volume is None else volume
        vol = max(0.02, min(1.0, float(vol)))
        caminho = os.path.join(BASE, f"aviso_{int(round(vol * 100))}.wav")
        if not os.path.exists(caminho) or os.path.getsize(caminho) < 1000:
            criar_som_aviso(caminho, vol)
        if sys.platform == "win32":
            import winsound
            winsound.PlaySound(caminho, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception as e:
        registrar(f"SOM: nao consegui tocar ({e})")
        try:
            if sys.platform == "win32":
                import winsound
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
        except Exception:
            pass
        return False


def evento_info(eventos, agora=None):
    """Proximo evento da lista (horario local): (nomes, inicio, fim, em_andamento)
    ou None. Se dois comecam na mesma hora, junta os nomes."""
    import datetime as dt
    agora = agora or dt.datetime.now()
    melhor = None  # [inicio, [nomes], andando, fim]
    for ev in eventos or []:
        try:
            minuto = int(ev.get("minuto", 0))
            horas = ev.get("horas")
            dias = ev.get("dias")
            dur = int(ev.get("duracao", 0))
            nome = str(ev.get("nome", "Evento"))
            nome_agora = str(ev.get("agora") or nome)
        except Exception:
            continue
        base = agora.replace(second=0, microsecond=0)
        for passo in range(-dur - 1, 60 * 24 * 8):
            t = base + dt.timedelta(minutes=passo)
            if t.minute != minuto:
                continue
            if horas is not None and t.hour not in horas:
                continue
            if dias is not None and t.weekday() not in dias:
                continue
            fim = t + dt.timedelta(minutes=max(dur, 1))
            if fim <= agora:
                continue
            andando = t <= agora < fim
            rot = nome_agora if andando else nome
            if melhor is None or (andando and not melhor[2]) or \
                    (andando == melhor[2] and t < melhor[0]):
                melhor = [t, [rot], andando, fim]
            elif t == melhor[0] and andando == melhor[2] and rot not in melhor[1]:
                melhor[1].append(rot)
                melhor[3] = max(melhor[3], fim)
            break
    if not melhor:
        return None
    t, nomes, andando, fim = melhor
    return " + ".join(nomes), t, fim, andando


def formatar_relogio(segundos):
    segundos = max(0, int(segundos))
    h, resto = divmod(segundos, 3600)
    m, s_ = divmod(resto, 60)
    return f"{h}:{m:02d}:{s_:02d}" if h else f"{m:02d}:{s_:02d}"


def formatar_tempo_farm(segundos):
    segundos = max(0, int(segundos))
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    return f"{horas:02d}:{minutos:02d}:{segundos:02d}"


def proximo_evento(eventos, agora=None):
    """Texto curto do proximo evento (usado nos testes)."""
    import datetime as dt
    agora = agora or dt.datetime.now()
    info = evento_info(eventos, agora)
    if not info:
        return "", False
    nomes, t, fim, andando = info
    if andando:
        return f"\u25cf {nomes} agora", True
    return f"\u25b8 {nomes} {formatar_relogio((t - agora).total_seconds())}", False


def tier_atual(qtd, tiers):
    """Estrela em que voce esta (1..n) e quanto falta para fechar ela, do jeito
    que o jogo mostra. Ex.: tiers [5,25,75] e 100 almas -> (3, 5)."""
    acum = 0
    for i, t in enumerate(tiers, 1):
        acum += t
        if qtd < acum:
            return i, acum - qtd
    return len(tiers), 0


def total_por_tier(estrela, faltam, tiers):
    """Inverso de tier_atual: estrela 3 e faltam 5 -> 100."""
    fim = sum(tiers[:estrela])
    tamanho = tiers[estrela - 1]
    return fim - max(0, min(int(faltam), tamanho))


def carregar_json(caminho, padrao):
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return padrao


def salvar_json(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(tmp, caminho)



def abrir_mss(mss):
    """mss novo usa mss.MSS; o antigo, mss.mss (que agora avisa que vai sair)."""
    return mss.MSS() if hasattr(mss, "MSS") else mss.mss()

def carregar_config():
    cfg = dict(CONFIG_PADRAO)
    salvo = carregar_json(ARQ_CONFIG, {})
    if salvo.get("versao_cfg", 0) < 3:
        # areas escolhidas em versoes antigas podiam estar erradas: detecta de novo
        salvo.pop("regiao", None)
        salvo.pop("escala", None)
    if salvo.get("versao_cfg", 0) < 4:
        # leitura mais rapida: escala 2 le igual e custa menos
        salvo.pop("escala_ocr", None)
        salvo.pop("intervalo", None)
    if salvo.get("versao_cfg", 0) < 5:
        salvo.pop("eventos", None)  # agenda nova (Kairah e Nahma)
    if salvo.get("versao_cfg", 0) < 7 and salvo.get("tiers") in (None, [5, 25, 74]):
        salvo["tiers"] = [5, 25, 75]  # a 3a estrela vai ate 75
    if salvo.get("versao_cfg", 0) < 8 and isinstance(salvo.get("regiao"), dict):
        # areas antigas cortavam o comeco das linhas (e o "x1." quebrado)
        r = dict(salvo["regiao"])
        mais = min(int(r.get("left", 0)), max(40, int(r.get("height", 200) * 0.2)))
        r["left"] = int(r["left"]) - mais
        r["width"] = int(r["width"]) + mais
        salvo["regiao"] = r
    if salvo.get("versao_cfg", 0) < 9:
        salvo.pop("max_linhas", None)  # agora 6 com rolagem
    if salvo.get("versao_cfg", 0) < 10 and isinstance(salvo.get("eventos"), list):
        # loja do Mercador Nuvem Negra (renova toda hora cheia)
        if not any(e.get("nome") == "Loja" for e in salvo["eventos"]):
            salvo["eventos"].insert(1, {"nome": "Loja", "agora": "Loja nova", "minuto": 0,
                                        "horas": None, "duracao": 5})
    cfg.update(salvo)
    cfg["versao_cfg"] = 10
    # a meta e sempre a soma das estrelas
    try:
        cfg["meta"] = sum(int(t) for t in cfg["tiers"])
    except Exception:
        cfg["tiers"], cfg["meta"] = [5, 25, 75], 105
    # garante que padroes novos de versoes novas entrem, mantendo os do usuario
    salvos = list(cfg.get("padroes") or [])
    cfg["padroes"] = [p for p in PADROES_PADRAO if p not in salvos] + salvos
    return cfg


# --------------------------------------------------------------------------
# Texto
# --------------------------------------------------------------------------
def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def normalizar(s):
    s = sem_acento(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def parecido(a, b):
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def linhas_iguais(a, b):
    """Duas linhas de chat (ja normalizadas) sao a mesma linha?
    Tolera erros de OCR e lixo do cenario atras do chat."""
    if a == b:
        return True
    return parecido(a, b) >= 0.75


_CORTE_INICIO = re.compile(
    r"^.*?\b(acquired|obtained|received|looted|got|obteve|recebeu|adquiriu|"
    r"conseguiu|coletou|absorveu|ganhou)\b[\s:]*(?=\S)", re.I)
_CORTE_FIM = re.compile(
    r"\s+(foi|foram|obtid\w*|adquirid\w*|recebid\w*|coletad\w*|absorvid\w*|"
    r"acquired|obtained|has been|was)\b.*$", re.I)
_MINUSC = {"de", "da", "do", "das", "dos", "e", "of", "the"}


def corrigir_acentos(palavra):
    """O OCR erra o 'ção'/'ções' do portugues em letra pequena:
    'Aberracaéo' / 'Aberracao' / 'Aberracão' -> 'Aberração'."""
    if len(palavra) < 5:
        return palavra
    w = re.sub(r"(?<=\w)[c\u00e7][a\u00e3\u00e1\u00e2\u00e0][e\u00e9\u00ea\u00f3\u00f4\u00f5]?[o\u00f3\u00f4\u00f50]$", "\u00e7\u00e3o", palavra)
    w = re.sub(r"(?<=\w)[c\u00e7][o\u00f3\u00f4\u00f5][e\u00e9]s$", "\u00e7\u00f5es", w)
    if w != palavra and re.search(r"[aeiou]rr?a\u00e7\u00e3o$|[aeiou]rr?a\u00e7\u00f5es$", w) is None \
            and not re.search(r"[aeiou]\u00e7(\u00e3o|\u00f5es)$", w):
        return palavra  # so corrige quando fica com cara de palavra (vogal + ção)
    # 'Aberracão' -> 'Aberração' (rr seguido de ç e comum: aberração, corrida...)
    return w


def limpar_nome(nome):
    nome = _CORTE_INICIO.sub("", nome or "")
    # "x1." grudado no fim do nome ('Kerubianxi.' / 'Kerubianxl;')
    nome = re.sub(r"(?<=[a-z\u00e0-\u00ff]{4})[x\u00d7][1iIlL|!t\u00b9][\W]*\s*$", "", nome)
    nome = _CORTE_FIM.sub("", nome)
    nome = re.sub(r"[\[\]\(\)\{\}<>\"'\u201c\u201d:;!?.,|*]+", " ", nome)
    nome = re.sub(r"\s+x\s*\d*\s*$", "", nome, flags=re.I)
    # erros comuns de OCR dentro de palavras: 0->o, 1->l
    L = r"[^\W\d_]"
    nome = re.sub(rf"(?<={L})0|0(?={L})", "o", nome)
    nome = re.sub(rf"(?<={L})1|1(?={L})", "l", nome)
    nome = re.sub(r"(\s+\d+)+\s*$", "", nome)  # numeros soltos no fim
    # corta o lixo do cenario que o OCR emenda no fim (ex.: "Zaif Mutante ONE Ol SO")
    bons = []
    for p in nome.split():
        limpo = re.sub(r"[^\w]", "", p)
        lixo = (len(limpo) <= 2 and limpo.lower() not in _MINUSC) or \
               (limpo.isupper() and len(limpo) <= 4 and bons)
        if lixo:
            break
        bons.append(p)
    while bons and bons[-1].lower() in _MINUSC:
        bons.pop()
    nome = " ".join(bons)
    nome = re.sub(r"\s+", " ", nome).strip(" -_")
    if len(normalizar(nome)) < 3:
        return ""
    partes = [corrigir_acentos(p) for p in nome.split(" ")]
    out = []
    for i, p in enumerate(partes):
        if i > 0 and p.lower() in _MINUSC:
            out.append(p.lower())
        else:
            out.append(p[:1].upper() + p[1:])
    return " ".join(out)


class Extrator:
    def __init__(self, padroes):
        self.regs = []
        self.erros = []
        for p in padroes:
            try:
                self.regs.append(re.compile(p, re.I))
            except re.error as e:
                self.erros.append(f"{p}: {e}")

    def nome_em(self, linha):
        if alma_de_outro(linha):
            return None
        tem_pontos = contem_aprox(linha, "pontos", 0.66)
        for r in self.regs:
            if "pontos" not in r.pattern and not tem_pontos:
                # o seu drop e sempre 'Obteve Pontos da Alma ...'; sem 'Pontos'
                # nao e alma sua (ex.: 'Fulano obteve Alma: X' do grupo)
                continue
            m = r.search(linha or "")
            if not m:
                continue
            g = m.groupdict().get("nome")
            if g is None and m.groups():
                g = m.group(1)
            nome = limpar_nome(g or "")
            if nome and len(compacto(nome)) >= 4:
                return nome
        return nome_aproximado(linha)


def _edicoes(a, b):
    """Quantas letras trocar/tirar/por para ir de a ate b (Levenshtein)."""
    if len(a) < len(b):
        a, b = b, a
    ant = list(range(len(b) + 1))
    for i, ca_ in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(ant[j] + 1, cur[j - 1] + 1, ant[j - 1] + (ca_ != cb)))
        ant = cur
    return ant[-1]


def mesmo_monstro(a, b):
    """a e b (normalizados) sao o mesmo monstro lido com erro de OCR?
    Compara palavra por palavra: cada palavra pode ter poucas letras erradas,
    mas uma palavra trocada ou a mais e outro monstro."""
    pa, pb = a.split(), b.split()
    if len(pa) != len(pb):
        # OCR juntou/separou palavras: so aceita se for quase identico
        ka, kb = "".join(pa), "".join(pb)
        return abs(len(ka) - len(kb)) <= 1 and _edicoes(ka, kb) <= 1
    total = 0
    for x, y in zip(pa, pb):
        e = _edicoes(x, y)
        maior = len(max(x, y, key=len))
        # ate 1 letra errada por palavra (2 em palavras de 10+ letras):
        # 'Kerubian' x 'Kerubiel' sao bichos diferentes
        ok = e <= max(1, maior // 5)
        if not ok:
            return False
        total += e
    return total <= max(2, len("".join(pa)) // 6)


def nome_mais_completo(novo, velho):
    """'Espirito da Terra Intermediario' completa 'Espirito da Terra Ermediario'
    (o comeco da palavra tinha ficado fora da leitura)?"""
    pn, pv = normalizar(novo).split(), normalizar(velho).split()
    if len(pn) != len(pv) or pn == pv:
        return False
    maior = False
    for x, y in zip(pn, pv):
        if x == y:
            continue
        if len(x) > len(y) >= 3 and x.endswith(y):
            maior = True
            continue
        return False
    return maior


OD_TIPOS = ["OD", "OD Fino", "OD Puro", "OD Radiante"]
OD_CORES = {"OD": "#c9ccd6", "OD Fino": "#7ddc7a", "OD Puro": "#6aa8ff", "OD Radiante": "#f2c94c"}


def od_em(linha):
    """'Voce obteve OD Fino x2.' -> ('OD Fino', 2). So o SEU drop (com 'Voce'),
    nunca o de outra pessoa do grupo. Quantidade 1 ou 2 (x1, x2, 1x, 2x)."""
    if not linha or alma_de_outro(linha):
        return None
    m = re.search(r"o[bp]t[eé]v[eé]\W{0,3}\s*([O0Q][D0O])(?![a-z])", linha, re.I)
    if not m:
        return None
    antes = linha[:m.start()]
    # alguem do grupo: 'Hagi obteve OD' (o seu e 'Voce obteve OD')
    if re.search(r"[A-Za-z\u00c0-\u00ff]{3,}", antes) and not contem_aprox(antes, "voce", 0.6):
        return None
    resto = linha[m.end():]
    tipo = "OD"
    pal = re.match(r"\W*([A-Za-z\u00c0-\u00ff]{3,})", resto)
    if pal:
        w = sem_acento(pal.group(1)).lower()
        for nome, alvo in (("OD Fino", "fino"), ("OD Puro", "puro"), ("OD Radiante", "radiante")):
            if parecido(w, alvo) >= 0.7:
                tipo = nome
                break
        else:
            return None  # 'OD' seguido de outra palavra: nao e o item
    q = re.search(r"(?:[x\u00d7]\s*([12])(?!\d)|(?<!\d)([12])\s*[x\u00d7](?![a-z]))", resto, re.I)
    qtd = int(q.group(1) or q.group(2)) if q else 1
    return tipo, qtd


def alma_de_outro(linha):
    """Alma que caiu para OUTRA pessoa do grupo:
    'Hagi obteve Alma: Kerubian (Vinculado) atraves da distribuicao do grupo.'
    Essas nao contam para voce."""
    if not linha:
        return False
    if contem_aprox(linha, "vinculado", 0.78) or contem_aprox(linha, "distribuicao", 0.78):
        return True
    # 'obteve Alma:' sem 'Pontos' = mensagem do grupo
    return bool(re.search(r"alma\s*[:;]", linha, re.I)) and not contem_aprox(linha, "pontos", 0.66)


def compacto(s):
    return normalizar(s).replace(" ", "")


def _melhor_janela(s, alvo, folga=1):
    """Procura em s (so letras) o trecho mais parecido com alvo.
    Devolve (nota, inicio, fim)."""
    n = len(alvo)
    melhor = (0.0, 0, 0)
    if len(s) < n - folga:
        return melhor
    for i in range(0, max(1, len(s) - n + folga + 1)):
        for L in range(n - folga, n + folga + 1):
            if i + L > len(s):
                continue
            r = difflib.SequenceMatcher(None, s[i:i + L], alvo).ratio()
            if r > melhor[0]:
                melhor = (r, i, i + L)
    return melhor


def contem_aprox(linha, alvo, minimo=0.8):
    s = re.sub(r"[^a-z]", "", sem_acento(linha or "").lower())
    return _melhor_janela(s, alvo)[0] >= minimo


def nome_aproximado(linha):
    """Reconhece 'Obteve Pontos da Alma <Nome> x1' mesmo com letras erradas
    pelo OCR (ex.: 'ObteveyPontos daAliraZaif,Mutante')."""
    linha = linha or ""
    letras, pos = [], []
    for i, ch in enumerate(linha):
        c = sem_acento(ch).lower()
        if "a" <= c <= "z":
            letras.append(c)
            pos.append(i)
    s = "".join(letras)
    nota, _, fim = _melhor_janela(s, "pontosdaalma")
    if nota < 0.75 or fim >= len(s):
        return None
    resto = linha[pos[fim]:]
    resto = re.split(r"\s*[x×X]\s*[\d¹]|[.;|](\s|$)|\s\|", resto)[0]
    nome = limpar_nome(resto)
    if len(compacto(nome)) < 4:
        return None
    return nome


# --------------------------------------------------------------------------
# Deteccao de linhas novas no chat
# --------------------------------------------------------------------------
def linhas_novas(P, C):
    """P = linhas do quadro anterior, C = linhas do quadro atual (normalizadas).
    Descobre quantas linhas o chat rolou e devolve os indices de C que sao novos.
    Devolve None quando nao da para alinhar (tela mudou, menu por cima etc.)."""
    if not P:
        return None
    for s in range(len(P)):  # s = quantas linhas antigas sumiram por cima
        sobre = min(len(P) - s, len(C))
        if sobre <= 0:
            break
        ok = sum(1 for i in range(sobre) if linhas_iguais(P[s + i], C[i]))
        precisa = max(1, -(-sobre * 3 // 4))  # 75%, arredondado para cima
        if ok >= precisa:
            return list(range(sobre, len(C)))
    return None


class Rastreador:
    """Recebe as linhas lidas a cada quadro e devolve os nomes de almas NOVAS."""
    JANELA_MEMORIA = 90.0  # segundos

    def __init__(self, padroes, canon=None, completar=None, comeco_de_outro=None):
        self.extrator = Extrator(padroes)
        # nome lido -> nome do monstro ja cadastrado (None se desconhecido)
        self.canon = canon or (lambda n: None)
        # pedaco do comeco do nome -> monstro cadastrado que comeca assim
        self.completar = completar or (lambda n: None)
        self.comeco_de_outro = comeco_de_outro or (lambda n: False)
        self.ant = None
        self.memoria = deque()  # (tempo, Counter de linhas de alma normalizadas)

    def reiniciar(self):
        self.ant = None
        self.mem = None
        self.lac = []
        self.passo = None

    def _lembrar(self, agora, linhas_alma):
        self.memoria.append((agora, Counter(linhas_alma)))
        while self.memoria and agora - self.memoria[0][0] > self.JANELA_MEMORIA:
            self.memoria.popleft()

    def _ja_visto(self, linha):
        maior = 0
        for _, cont in self.memoria:
            for k, v in cont.items():
                if linhas_iguais(k, linha):
                    maior = max(maior, v)
        return maior

    def quadro(self, linhas, agora=None):
        agora = time.time() if agora is None else agora
        C = [(normalizar(l), l) for l in linhas]
        C = [c for c in C if len(c[0]) >= 3]
        if not C:  # chat sumiu/esmaeceu: mantem a referencia anterior
            return []
        nomes_C = []
        for _, raw in C:
            n = self.extrator.nome_em(raw)
            if n:
                c = self.canon(n)
                if c is None:
                    # monstro ainda nao cadastrado: so aceita leitura nitida
                    nitida = re.search(r"pontos\s+da\s+alma\s+\S", raw, re.I) or \
                        re.search(r"(alma\s+d[eoa]s?|soul\s+of)\s+\S", raw, re.I)
                    c = n if nitida else None
                n = c
            nomes_C.append(n)
        # So usa mensagens do sistema ("Voce obteve...", almas) para alinhar.
        # Conversas de jogadores e lixo do cenario ficam de fora.
        par = [(c, n) for c, n in zip(C, nomes_C) if n or contem_aprox(c[1], "obteve")]
        if not par:
            return []
        # linha de alma vira uma chave limpa: o lixo do cenario no fim da
        # linha muda a cada quadro e nao pode atrapalhar a comparacao
        C = [("alma " + compacto(n), raw) if n else (k, raw) for (k, raw), n in par]
        nomes_C = [n for _, n in par]
        P, self.ant = self.ant, C

        resultado = []
        if P is not None:
            idx = linhas_novas([p[0] for p in P], [c[0] for c in C])
            if idx is not None:
                resultado = [nomes_C[i] for i in idx if nomes_C[i]]
            else:
                # Sem alinhamento (ex.: voltou do menu ESC). So conta linhas de
                # alma que aparecem MAIS vezes do que ja foram vistas recentemente.
                cont = Counter(C[i][0] for i, n in enumerate(nomes_C) if n)
                ja_contado = Counter()
                for i, n in enumerate(nomes_C):
                    if not n:
                        continue
                    k = C[i][0]
                    if cont[k] - self._ja_visto(k) - ja_contado[k] > 0:
                        resultado.append(n)
                        ja_contado[k] += 1

        self._lembrar(agora, [C[i][0] for i, n in enumerate(nomes_C) if n])
        return resultado

    # ------------------------------------------------------------------
    # Contagem pela POSICAO das linhas (aguenta rajadas de linhas iguais)
    # ------------------------------------------------------------------
    def _classificar(self, raw):
        """Devolve (chave, nome_da_alma_ou_None) para linhas do sistema,
        ou None para linhas que nao interessam (conversa, lixo do cenario)."""
        k = normalizar(raw)
        if len(k) < 3:
            return None
        od = od_em(raw)
        if od:
            return ("alma od_" + od[0].lower().replace(" ", "_"), "\u00a7OD:" + od[0])
        n = self.extrator.nome_em(raw)
        if n:
            if _TEM_X1.search(raw):
                c = self.canon(n)
            else:
                # sem o "x1." no fim: o nome quebrou de linha e o resto nao foi
                # lido ("...Alma Kalgolem de" / "Drakana x1."). So conta se der
                # para completar com um monstro que voce ja tem; nunca cria um
                # monstro novo com o nome cortado.
                c = self.completar(n) or self.canon(n)
                if c is None:
                    # se o resto nunca aparecer, conta com este nome depois
                    # de alguns quadros (o "x1." pode ter ficado fora da area)
                    cortado = re.search(r"\s(de|da|do|dos|das|e)\W*$", raw.strip(), re.I) \
                        or self.comeco_de_outro(n)
                    return ("alma " + compacto(n), None, None if cortado else n)
            if c is None:
                nitida = re.search(r"pontos\s+da\s+alma\s+\S", raw, re.I) or \
                    re.search(r"(alma\s+d[eoa]s?|soul\s+of)\s+\S", raw, re.I)
                c = n if nitida else None
            if c:
                return ("alma " + compacto(c), c)
            # monstro novo lido borrado: guarda como pendente (nao conta ainda,
            # mas ocupa o lugar certo e conta quando for lido limpo)
            return ("alma " + compacto(n), None)
        if contem_aprox(raw, "voceobteve", 0.8) or (n and contem_aprox(raw, "pontosdaalma")):
            return ("sis " + _trecho_util(k), None)
        return None

    def _marcar_vistos(self, cur, agora, fq, passo):
        """Impressao digital do chat: cada alma junto com as 2 mensagens com
        numero acima dela ('Voce obteve 463 Kinah') e a distancia ate elas.
        Se a mesma combinacao ja apareceu num quadro ANTERIOR, a alma e velha
        (voce subiu o chat e voltou).
        Devolve os indices de cur que sao velhos e guarda os deste quadro."""
        vistos = getattr(self, "vistos", None)
        if vistos is None:
            vistos = self.vistos = {}

        def fp(c):
            k = c["chave"]
            if k.startswith("sis "):
                num = re.findall(r"\d{2,}", k)
                return ("#" + num[0] + re.sub(r"[^a-z]", "", k[4:])[:6]) if num else k
            return k

        F = [fp(c) for c in cur]
        ja, novos_fp = set(), []
        for i in range(len(cur)):
            if F[i].startswith("#"):
                continue  # so interessam as almas
            # as 2 linhas com numero mais proximas acima (ate 10 linhas) e a
            # que distancia estao: aguenta o OCR perder linhas no meio. Um
            # valor de Kinah sozinho se repete (farmando o mesmo bicho cai 463
            # de novo); dois, na mesma distancia, nao.
            marcas = []
            for j in range(i - 1, -1, -1):
                dist = (cur[i]["y"] - cur[j]["y"]) / passo
                if dist > 12.5:
                    break
                if F[j].startswith("#"):
                    marcas.append((int(round(dist)), F[j]))
                    if len(marcas) == 3:
                        break
            # pares entre as 3 mais proximas: se o OCR perder uma, outro par
            # ainda bate
            for x in range(len(marcas)):
                for y in range(x + 1, len(marcas)):
                    seq = (F[i], marcas[x], marcas[y])
                    if seq in vistos and vistos[seq][0] < fq:
                        ja.add(i)
                    novos_fp.append(seq)
        for t in novos_fp:
            if t not in vistos:
                vistos[t] = (fq, agora)
        if len(vistos) > 20000 or getattr(self, "_limpa_vistos", 0) < agora - 60:
            self._limpa_vistos = agora
            for t in [k for k, v in vistos.items() if v[1] < agora - 600]:
                del vistos[t]
        return ja

    def quadro_pos(self, itens, agora=None):
        """itens = [(texto, y_centro, altura_do_texto), ...] em pixels.

        Encaixa o quadro novo no que ja conhecemos comparando o CONTEUDO
        (sobretudo os valores de Kinah, que sao unicos): descobre quanto o chat
        se mexeu (pode subir, descer ou nao mexer, ex.: quando voce abre o chat
        e aparece a caixa de digitar). Uma alma so conta como nova se aparecer
        ABAIXO da ultima linha conhecida - que e onde o jogo poe mensagem nova.
        Historico que aparece em cima (chat aberto) e linhas borradas no meio
        nunca contam."""
        agora = time.time() if agora is None else agora
        self._quadro = getattr(self, "_quadro", 0) + 1
        fq = self._quadro

        cur = []
        for t, y, h in juntar_quebradas(itens):
            c = self._classificar(t)
            if c:
                cur.append({"y": float(y), "h": h, "chave": c[0], "nome": c[1],
                            "qtd": (od_em(t) or (0, 1))[1] if (c[1] or "").startswith("\u00a7OD:")
                            else (quantidade_alma(t) if c[1] else 1), "cont": 0,
                            "prov": c[2] if len(c) > 2 else None})
        if not cur:
            if getattr(self, "mem", None) is None:
                self._vazio_no_inicio = getattr(self, "_vazio_no_inicio", 0) + 1
            return []  # chat escondido/coberto: guarda tudo como estava
        cur.sort(key=lambda c: c["y"])
        alt = sorted(c["h"] for c in cur)[len(cur) // 2]

        # distancia entre linhas (so para tolerancias)
        estim = []
        for a, b in zip(cur, cur[1:]):
            d = b["y"] - a["y"]
            n = max(1, int(round(d / (alt * 1.8))))
            if d > alt * 0.9:
                estim.append(d / n)
        if estim:
            d = sorted(estim)[len(estim) // 2]
            ant_p = getattr(self, "passo", None)
            self.passo = d if not ant_p else 0.8 * ant_p + 0.2 * d
        passo = getattr(self, "passo", None) or alt * 1.8
        tol = passo * 0.4

        # duas leituras no mesmo lugar = mesma linha (fica a mais confiavel)
        limpo = []
        for c in cur:
            if limpo and c["y"] - limpo[-1]["y"] < tol:
                if c["nome"] and not limpo[-1]["nome"]:
                    limpo[-1] = c
                continue
            limpo.append(c)
        cur = limpo

        # Impressao digital: as mensagens com numero ("Voce obteve 463 Kinah")
        # sao praticamente unicas. Se uma delas ja passou pela tela antes, o
        # trecho em volta e velho (voce subiu o chat e voltou), nao e matanca.
        ja_vi = self._marcar_vistos(cur, agora, fq, getattr(self, "passo", None) or
                                    sorted(c["h"] for c in cur)[len(cur) // 2] * 1.8)

        def velha(i, entre=None):
            return i in ja_vi

        mem = getattr(self, "mem", None)
        if mem is None or not mem:
            self.mem = [dict(c, visto=fq, pend=False) for c in cur]
            self.lac = []
            # o chat estava escondido e apareceu: quem fez ele aparecer foi a
            # mensagem nova, que e a linha mais de baixo
            if getattr(self, "_vazio_no_inicio", 0) >= 2 and cur[-1]["nome"] \
                    and not velha(len(cur) - 1):
                self.mem[-1]["cont"] = cur[-1]["qtd"]
                return [cur[-1]["nome"]] * cur[-1]["qtd"]
            return []  # primeira vez: so referencia

        # ---- 1) candidatos de deslocamento (quanto o chat se mexeu) ----
        cands = {0.0}
        for m in mem:
            for c in cur:
                if _diferenca_linhas(m["chave"], c["chave"]) < 1.0:
                    cands.add(m["y"] - c["y"])
        grupos = {}
        for d in cands:
            grupos.setdefault(round(d / (passo * 0.25)), []).append(d)
        cands = [sorted(g)[len(g) // 2] for g in grupos.values()]

        fundo_mem = max(m["y"] for m in mem)
        topo_mem = min(m["y"] for m in mem)
        lac = getattr(self, "lac", [])

        def avaliar(delta):
            fundo = fundo_mem - delta
            topo = topo_mem - delta
            usados, pares, novos, meio = set(), [], [], []
            sc = 0.0
            for i, c in enumerate(cur):
                melhor, dist = None, tol
                for j, m in enumerate(mem):
                    if j in usados:
                        continue
                    dd = abs(m["y"] - delta - c["y"])
                    if dd < dist:
                        melhor, dist = j, dd
                if melhor is not None:
                    usados.add(melhor)
                    m = mem[melhor]
                    sc += _peso_encaixe(m["chave"], c["chave"])
                    pares.append((i, melhor))
                elif c["y"] > fundo + passo * 0.5:
                    sc -= 0.15
                    novos.append(i)
                elif c["y"] < topo - passo * 0.5:
                    sc -= 0.6  # historico que apareceu em cima
                else:
                    perto_lac = any(abs(ly - delta - c["y"]) < tol for ly, _ in lac)
                    sc -= 0.1 if perto_lac else 0.5
                    meio.append(i)
            vis_min, vis_max = cur[0]["y"] - tol, cur[-1]["y"] + tol
            for j, m in enumerate(mem):
                if j not in usados and m["visto"] == fq - 1 and vis_min <= m["y"] - delta <= vis_max:
                    sc -= 0.3  # sumiu (o OCR pode ter perdido)
            return sc, pares, novos, meio

        melhor = None
        for d in cands:
            sc, pares, novos, meio = avaliar(d)
            if abs(d) < tol:
                sc += 0.5  # o mais comum e o chat nao ter se mexido
            chave_ord = (sc, -abs(d))
            if melhor is None or chave_ord > melhor[0]:
                melhor = (chave_ord, d, pares, novos, meio)
        (sc, _), delta, pares, novos, meio = melhor

        # quadro que nao encaixa em nada do que conhecemos
        sem_correspondencia = not any(
            _peso_encaixe(mem[j]["chave"], cur[i]["chave"]) > 0 for i, j in pares)
        if (sc < -3 and len(novos) >= 3) or (sem_correspondencia and len(cur) >= 4):
            conf = getattr(self, "_confuso", None)
            self._confuso = cur
            if conf and _mesmo_quadro(conf, cur, tol):
                # dois quadros seguidos mostram o mesmo chat novo: adota.
                # Se o fundo do chat ficou no mesmo lugar, foi rajada maior
                # que o chat: as almas visiveis sao todas novas.
                self.mem = [dict(c, visto=fq, pend=False) for c in cur]
                self.lac = []
                self._confuso = None
                if abs(cur[-1]["y"] - fundo_mem) < tol:
                    out = []
                    for i in range(len(self.mem) - 1, -1, -1):
                        m = self.mem[i]
                        # A primeira leitura da rajada ja registrou suas
                        # marcas. Isso nao significa que ela foi contada.
                        era_velha = any(abs(c["y"] - m["y"]) < tol
                                        for c in getattr(self, "_confuso_velhas", []))
                        if m["nome"] and not era_velha:
                            m["cont"] = m["qtd"]
                            out += [m["nome"]] * m["qtd"]
                    return out
            else:
                self._confuso_velhas = [dict(cur[i]) for i in ja_vi]
            return []
        self._confuso = None

        contados = []

        def contar(c, ja=0):
            # "x2" no fim = 2 almas de uma vez
            n = c["qtd"] - ja
            if n > 0:
                contados.extend([c["nome"]] * n)
                c["cont"] = c["qtd"]

        # almas novas (abaixo da ultima linha conhecida)
        novos_set = set(novos)
        for i in novos:
            if cur[i]["nome"] and not velha(i, novos_set):
                contar(cur[i])
        # almas novas que o OCR tinha perdido e agora apareceram no meio
        lac_rest = []
        usados_lac = set()
        for i in meio:
            c = cur[i]
            for k, (ly, ttl) in enumerate(lac):
                if k not in usados_lac and abs(ly - delta - c["y"]) < tol:
                    usados_lac.add(k)
                    if c["nome"] and not velha(i, novos_set | set(meio)):
                        contar(c)
                    break
        # alma de monstro novo lida borrada que agora foi confirmada
        for i, j in pares:
            m, c = mem[j], cur[i]
            if m.get("pend") and not c["nome"] and c.get("prov"):
                c["inc"] = m.get("inc", 0) + 1
                if c["inc"] >= 4 and compacto(c["prov"]) == m["chave"][5:]:
                    c["nome"] = c["prov"]  # conta logo abaixo
            if m.get("pend") and c["nome"]:
                contar(c)
            elif c["nome"] and m.get("cont", 0) > 0:
                # a mesma linha lida de novo: se antes o OCR viu "x1" e agora
                # ve "x2", soma a diferenca (nunca diminui)
                c["cont"] = m["cont"]
                if c["qtd"] > m["cont"] and c["nome"] == m["nome"]:
                    contar(c, m["cont"])

        # ---- 2) atualiza a memoria ----
        nova_mem = []
        casados = {j for _, j in pares}
        for i, j in pares:
            m, c = mem[j], cur[i]
            nome = c["nome"] or m["nome"]
            pend = bool(m.get("pend") and not c["nome"])
            inc = c.get("inc", 0)
            nova_mem.append({"y": c["y"], "h": c["h"], "chave": c["chave"] if c["nome"] or not m["nome"] else m["chave"],
                             "nome": nome, "visto": fq, "pend": pend,
                             "qtd": max(c.get("qtd", 1), m.get("qtd", 1)),
                             "cont": max(c.get("cont", 0), m.get("cont", 0)), "inc": inc})
        for i in novos + meio:
            c = cur[i]
            pend = (i in novos) and c["nome"] is None and c["chave"].startswith("alma ")
            nova_mem.append(dict(c, visto=fq, pend=pend))
        for i, c in enumerate(cur):
            if i not in novos and i not in meio and all(i != a for a, _ in pares):
                nova_mem.append(dict(c, visto=fq, pend=False))  # historico revelado
        for j, m in enumerate(mem):
            if j not in casados and fq - m["visto"] < 8:
                nova_mem.append(dict(m, y=m["y"] - delta))
        nova_mem.sort(key=lambda m: m["y"])
        junta = []
        for m in nova_mem:
            if junta and m["y"] - junta[-1]["y"] < tol:
                if m["visto"] > junta[-1]["visto"]:
                    junta[-1] = m
                continue
            junta.append(m)
        self.mem = junta[-60:]

        # lacunas: lugares abaixo do fundo antigo onde deveria haver linha nova
        # mas o OCR nao leu nada (pode ser alma que aparece no proximo quadro)
        nl = [(ly - delta, ttl - 1) for k, (ly, ttl) in enumerate(lac)
              if k not in usados_lac and ttl > 1]
        if novos:
            fundo = fundo_mem - delta
            ultimo = max(cur[i]["y"] for i in novos)
            y = fundo + passo
            while y < ultimo - tol:
                if not any(abs(c["y"] - y) < tol for c in cur):
                    nl.append((y, 6))
                y += passo
        self.lac = nl
        return contados


def _mesmo_quadro(a, b, tol):
    """Os dois quadros mostram o mesmo chat (mesmas linhas, mesmos lugares)?"""
    if not a or not b:
        return False
    bate = 0
    for c in b:
        for d in a:
            if abs(d["y"] - c["y"]) < tol and _peso_encaixe(d["chave"], c["chave"]) > 0:
                bate += 1
                break
    return bate >= max(2, int(len(b) * 0.6))


def _peso_encaixe(a, b):
    """Quanto vale dizer que a linha 'a' (memoria) e a linha 'b' (agora) sao
    a mesma. Linhas que se repetem (almas iguais) quase nao provam nada; o
    valor do Kinah e unico e decide."""
    alma_a, alma_b = a.startswith("alma "), b.startswith("alma ")
    if alma_a and alma_b:
        r = 1.0 if a == b else parecido(a, b)
        if r < 0.75 and _comeco_do_mesmo(a, b):
            return 0.3  # a mesma linha quebrada, com a 2a metade perdida
        return 0.6 if r == 1.0 else (0.4 if r >= 0.75 else -3.0)
    if alma_a != alma_b:
        return -3.0
    na, nb = re.findall(r"\d{2,}", a), re.findall(r"\d{2,}", b)
    if na and nb:
        x, y = na[0], nb[0]
        if x == y:
            return 3.0
        if len(x) == len(y) and sum(p != q for p, q in zip(x, y)) == 1:
            return 1.0   # um digito trocado: erro comum de OCR (3/2, 1/7...)
        if x.startswith(y) or y.startswith(x):
            return 0.0   # OCR comeu um digito
        return -2.5
    if a == b:
        return 1.0
    r = parecido(a, b)
    return 0.6 if r >= 0.75 else -0.3


def _trecho_util(k):
    """Das linhas 'Voce obteve X...' fica so o que vem logo depois de
    'obteve' (3 palavras). O resto costuma ser lixo do cenario."""
    tok = k.split()
    i = None
    for j, t in enumerate(tok):
        if "obteve" in t or parecido(t[-6:], "obteve") >= 0.6:
            i = j
            break
    if i is None:
        return " ".join(tok[:4])
    return " ".join(tok[i + 1:i + 4])


_QTD_ALMA = re.compile(r"[x\u00d7]\s*([2-5])\s*[.,:;]?(\s|$)", re.I)


def quantidade_alma(linha):
    """'... Kalgolem x2.' -> 2. So aceita 2 a 5: o OCR le muito o 'x1' como
    'x7', 'xl', 'xi', entao qualquer outra coisa vale 1."""
    m = _QTD_ALMA.search(linha or "")
    return int(m.group(1)) if m else 1


_TEM_X1 = re.compile(r"[x\u00d7]\s*[\d1lIi|\u00b9]{1,3}\s*[.,:;]?(\s|$)", re.I)
_FIM_ALMA = re.compile(r"[x\u00d7]\s*[\d1lIi|]{1,3}\s*[.,:;]?\s*\S{0,6}\s*$", re.I)


def _sem_lixo_no_fim(t):
    """Tira o lixo do cenario que o OCR pega no fim da 1a metade de uma linha
    quebrada ('...Espirito do Fogo o' -> '...Espirito do Fogo'), mantendo
    'de/da/do' (o nome continua na linha de baixo)."""
    tok = t.rstrip().split()
    while len(tok) > 1:
        w = re.sub(r"[^\w]", "", tok[-1])
        if w.lower() in ("de", "da", "do", "dos", "das"):
            break
        if len(w) <= 2 or not w[:1].isupper() or w.isupper() or re.search(r"\d", w) \
                or re.search(r"[^\w\-]", tok[-1]):
            tok.pop()
            continue
        break
    return " ".join(tok)


def juntar_quebradas(itens):
    """Nome de monstro comprido quebra a mensagem em duas linhas:
    'Obteve Pontos da Alma Kalgolem de' / 'Drakana x1.'. Junta as duas."""
    itens = sorted(itens, key=lambda x: x[1])
    out, usados = [], set()
    for i, (t, y, h) in enumerate(itens):
        if i in usados:
            continue
        if contem_aprox(t, "pontosdaalma", 0.8) and not _FIM_ALMA.search(t):
            for j in range(i + 1, min(i + 3, len(itens))):
                t2, y2, h2 = itens[j]
                if y2 - y > max(h, h2) * 2.6:
                    break
                if _FIM_ALMA.search(t2) and len(normalizar(t2)) <= 30 and \
                        not contem_aprox(t2, "obteve", 0.8):
                    t = _sem_lixo_no_fim(t) + " " + t2.strip()
                    usados.add(j)
                    break
        out.append((t, y, h))
    return out


def _comeco_do_mesmo(a, b):
    """'alma espiritodaterra' e 'alma espiritodaterraintermediario': a mesma
    alma, uma lida inteira e a outra sem a 2a linha (nome quebrado)."""
    a, b = a[5:], b[5:]
    if len(a) > len(b):
        a, b = b, a
    return len(a) >= 8 and len(b) > len(a) and parecido(b[:len(a)], a) >= 0.9


def _diferenca_linhas(a, b):
    """Custo de dizer que a linha 'a' (quadro anterior) e a mesma linha 'b'.
    0 = com certeza a mesma; >=4 = com certeza diferentes (conflito)."""
    if a == b:
        return 0.0
    alma_a, alma_b = a.startswith("alma "), b.startswith("alma ")
    if alma_a and alma_b:
        r = parecido(a, b)
        if r < 0.75 and _comeco_do_mesmo(a, b):
            return 0.8
        return (1 - r) * 2 if r >= 0.75 else 10.0
    if alma_a != alma_b:
        return 6.0  # alma de um lado, outra mensagem do outro
    # duas mensagens 'Voce obteve ...': o OCR erra bastante nelas, entao a
    # diferenca vale como pista, nao como certeza
    na, nb = re.findall(r"\d{2,}", a), re.findall(r"\d{2,}", b)
    if na and nb and na != nb:
        return 1.2  # valor de Kinah diferente (ou OCR leu errado)
    r = parecido(a, b)
    if r >= 0.6:
        return (1 - r) * 1.5
    return 1.0


# --------------------------------------------------------------------------
# Progresso (dados salvos)
# --------------------------------------------------------------------------
class Progresso:
    def __init__(self, arquivo):
        self.arquivo = arquivo
        dados = carregar_json(arquivo, {"monstros": []})
        self.monstros = dados.get("monstros", [])
        ocultos = dados.get("pets_ocultos", {})
        self.pets_ocultos = ({str(k): int(v) for k, v in ocultos.items()}
                             if isinstance(ocultos, dict) else
                             {str(k): 0 for k in ocultos})
        self.pet_estado = {str(k): int(v) for k, v in
                           (dados.get("pet_estado") or {}).items()}
        self.pet_estado_em = dados.get("pet_estado_em")
        self.od = {t: int((dados.get("od") or {}).get(t, 0)) for t in OD_TIPOS}
        self.materiais = {str(k): max(0, int(v)) for k, v in
                          (dados.get("materiais") or {}).items()}
        # almas por dia do jogo (o dia vira no reset das 04:00)
        self.dias = dados.get("dias", {}) if isinstance(dados.get("dias"), dict) else {}
        for m in self.monstros:
            m.setdefault("qtd", 0)
            m.setdefault("apelidos", [])
            m.setdefault("atualizado", 0)
            if not m.get("fixo"):
                # nomes salvos com o erro de OCR do 'ção' (ex.: 'Aberracaéo')
                novo = " ".join(corrigir_acentos(p) for p in m["nome"].split(" "))
                if novo != m["nome"]:
                    m["apelidos"].append(normalizar(m["nome"]))
                    m["nome"] = novo
                    m.pop("votos", None)
        # Versões anteriores colocavam o catálogo inteiro na aba de farm.
        # Essas entradas não foram escolhidas nem receberam drop durante o uso.
        migrou_catalogo = False
        for m in list(self.monstros):
            if (m.get("pet_id") and not m.get("atualizado") and
                    not m.get("ultima_alma")):
                pet_id = str(m["pet_id"])
                self.pet_estado.setdefault(pet_id, int(m["qtd"]))
                self.monstros.remove(m)
                migrou_catalogo = True
        if migrou_catalogo:
            self.salvar()

    def salvar(self):
        try:
            salvar_json(self.arquivo, {"monstros": self.monstros, "dias": self.dias,
                                      "od": self.od, "materiais": self.materiais,
                                      "pets_ocultos": self.pets_ocultos,
                                      "pet_estado": self.pet_estado,
                                      "pet_estado_em": self.pet_estado_em})
        except Exception:
            pass

    def _chaves(self, m):
        return [normalizar(m["nome"])] + [normalizar(a) for a in m["apelidos"]]

    def achar(self, nome):
        n = normalizar(nome)
        k = n.replace(" ", "")
        for m in self.monstros:
            if k in [c.replace(" ", "") for c in self._chaves(m)]:
                return m
        # Corrige erros pequenos sem escolher entre candidatos ambiguos.
        # Apelidos so valem na busca exata: nao encadear aproximacoes.
        candidatos = []
        for m in self.monstros:
            c = normalizar(m["nome"])
            if mesmo_monstro(n, c):
                r = parecido(k, c.replace(" ", ""))
                candidatos.append((r, m))
        candidatos.sort(key=lambda par: par[0], reverse=True)
        if not candidatos:
            return None
        if len(candidatos) > 1 and candidatos[0][0] - candidatos[1][0] < 0.08:
            return None  # nao atribuir uma leitura ambigua a um bicho arbitrario
        return candidatos[0][1]

    def canonico(self, nome):
        m = self.achar(nome)
        return m["nome"] if m else None

    def completar(self, pedaco):
        """'Kalgolem de' -> 'Kalgolem de Drakana' (o unico monstro cadastrado
        cujo nome comeca com esse pedaco). Se houver varios ('Espirito da Agua
        Superior' e '... Intermediario'), nao chuta: devolve None."""
        k = compacto(pedaco)
        if len(k) < 5:
            return None
        achados = []
        for m in self.monstros:
            for c in [compacto(m["nome"])] + [a.replace(" ", "") for a in m["apelidos"]]:
                if len(c) > len(k) and (c.startswith(k) or parecido(c[:len(k)], k) >= 0.88):
                    achados.append(m)
                    break
        if len(achados) != 1:
            return None
        return achados[0]["nome"]

    def comeco_de_outro(self, pedaco):
        """O pedaco e o comeco de algum monstro cadastrado (nome cortado)?"""
        k = compacto(pedaco)
        return any(c.replace(" ", "").startswith(k) and len(c.replace(" ", "")) > len(k)
                   for m in self.monstros for c in self._chaves(m))

    def somar(self, nome, n=1, salvar=True):
        m = self.achar(nome)
        if m is None:
            m = {"nome": nome, "qtd": 0, "apelidos": [], "atualizado": 0}
            self.monstros.append(m)
        if n > 0 and not m.get("fixo"):
            votos = m.setdefault("votos", {})
            if nome_mais_completo(nome, m["nome"]):
                # o nome salvo estava cortado ('Ermediario'): a leitura inteira
                # ('Intermediario') vale na hora, sem esperar votos
                antigo = normalizar(m["nome"])
                if antigo not in m["apelidos"]:
                    m["apelidos"].append(antigo)
                votos.clear()
                votos[nome] = 1000
            # o nome mostrado e a grafia que o OCR leu mais vezes
            votos[nome] = votos.get(nome, 0) + 1
            m["nome"] = max(votos, key=votos.get)
        m["qtd"] = max(0, m["qtd"] + n)
        m["atualizado"] = time.time()
        if n > 0:
            m["ultima_alma"] = m["atualizado"]
            self._anotar_dia(m["nome"], n)
        if salvar:
            self.salvar()
        return m

    def somar_pet(self, pet_id, nome, n):
        """Usa o identificador do jogo; nomes parecidos nunca sao misturados."""
        pet_id = str(pet_id)
        anterior_oculto = self.pets_ocultos.pop(pet_id, 0)
        confirmado = self.pet_estado.get(pet_id)
        m = next((x for x in self.monstros if x.get("pet_id") == pet_id), None)
        if m is None:
            m = next((x for x in self.monstros if not x.get("pet_id")
                      and normalizar(x["nome"]) == normalizar(nome)), None)
        if m is None:
            # Ao aparecer pela primeira vez na lista, começa no total que já foi
            # sincronizado da conta, em vez de começar em zero.
            base = confirmado if confirmado is not None else anterior_oculto
            m = {"nome": nome, "qtd": base, "apelidos": [], "atualizado": 0}
            self.monstros.append(m)
        elif confirmado is not None and m["qtd"] != confirmado:
            # O estado completo do jogo é a fonte correta caso a linha local
            # tenha ficado antiga ou tenha sido criada antes da sincronização.
            m["qtd"] = confirmado
        m["pet_id"] = pet_id
        m["qtd"] += n
        # Todo ganho recebido pelo Npcap também atualiza o total exibido na
        # coleção, mesmo antes de chegar uma fotografia completa da conta.
        self.pet_estado[pet_id] = min(105, m["qtd"])
        m["atualizado"] = m["ultima_alma"] = time.time()
        self._anotar_dia(m["nome"], n)
        return m

    def sincronizar_pets(self, levels, points, nomes):
        """Importa o progresso atual do jogo sem contar o login como drop novo."""
        alterou = False
        estado = {}
        for raw_id in levels.keys() | points.keys():
            pet_id = str(raw_id)
            nivel = max(0, min(int(levels.get(raw_id, 0)), 3))
            total = sum((5, 25, 75)[:nivel]) + (0 if nivel == 3 else
                                                     min(max(0, int(points.get(raw_id, 0))),
                                                         (5, 25, 75)[nivel]))
            estado[pet_id] = total
            if pet_id in self.pets_ocultos:
                if self.pets_ocultos[pet_id] != total:
                    self.pets_ocultos[pet_id] = total
                    alterou = True
                continue
            nome = nomes.get(pet_id)
            m = next((x for x in self.monstros if x.get("pet_id") == pet_id), None)
            if m is None and nome:
                m = next((x for x in self.monstros if not x.get("pet_id")
                          and normalizar(x["nome"]) == normalizar(nome)), None)
            if m is None:
                continue
            if m["qtd"] != total or m.get("pet_id") != pet_id:
                m["qtd"] = total
                m["pet_id"] = pet_id
                alterou = True
        novo_estado = {pet_id: estado.get(pet_id, 0) for pet_id in nomes}
        novo_estado.update(estado)
        alterou = alterou or novo_estado != self.pet_estado
        self.pet_estado = novo_estado
        self.pet_estado_em = time.time()
        return alterou

    def catalogo_pets(self, nomes, tiers=(5, 25, 75)):
        """Todas as entradas conhecidas, com valor confirmado ou salvo localmente."""
        ids = set(nomes) | set(self.pet_estado) | set(self.pets_ocultos)
        salvos = {str(m["pet_id"]): m for m in self.monstros if m.get("pet_id")}
        ids.update(salvos)
        linhas = []
        for pet_id in ids:
            nome = nomes.get(pet_id) or salvos.get(pet_id, {}).get("nome") or f"Pet #{pet_id}"
            if pet_id in self.pet_estado:
                total = self.pet_estado[pet_id]
                origem = "jogo" if self.pet_estado_em is not None else "salvo"
            elif pet_id in salvos:
                total, origem = salvos[pet_id]["qtd"], "salvo"
            elif pet_id in self.pets_ocultos:
                total, origem = self.pets_ocultos[pet_id], "salvo"
            else:
                total, origem = None, "desconhecido"
            if total is None:
                andamento, falta = "—", "—"
            else:
                estrela, faltam = tier_atual(total, tiers)
                andamento = (f"Nv.{len(tiers)} MAX" if total >= sum(tiers) else
                             f"Nv.{estrela - 1} "
                             f"{tiers[estrela - 1] - faltam}/{tiers[estrela - 1]}")
                falta = str(max(0, sum(tiers) - total))
            linhas.append((pet_id, nome, andamento, falta, origem))
        return sorted(linhas, key=lambda x: normalizar(x[1]))

    def _anotar_dia(self, nome, n):
        import datetime as dt
        k = chave_dia(dt.datetime.now())
        dia = self.dias.setdefault(k, {})
        dia[nome] = dia.get(nome, 0) + n
        if len(self.dias) > 21:  # guarda 3 semanas
            for velho in sorted(self.dias)[:-21]:
                del self.dias[velho]

    def estatisticas(self, agora=None):
        import datetime as dt
        agora = agora or dt.datetime.now()
        hoje = self.dias.get(chave_dia(agora), {})
        ini = chave_dia(inicio_semana(agora) + dt.timedelta(minutes=1))
        fim = chave_dia(agora)
        semana = sum(sum(v.values()) for k, v in self.dias.items() if ini <= k <= fim)
        return {"hoje": sum(hoje.values()), "semana": semana,
                "total": sum(m["qtd"] for m in self.monstros),
                "top_hoje": sorted(hoje.items(), key=lambda kv: -kv[1])[:5]}

    def definir(self, m, qtd):
        m["qtd"] = max(0, int(qtd))
        m["atualizado"] = time.time()
        self.salvar()

    def renomear(self, m, novo):
        antigo = normalizar(m["nome"])
        if antigo not in m["apelidos"]:
            m["apelidos"].append(antigo)
        m["nome"] = novo
        m["fixo"] = True  # nome escolhido pelo usuario nao muda mais sozinho
        self.salvar()

    def juntar(self, origem, destino):
        destino["qtd"] += origem["qtd"]
        for a in [normalizar(origem["nome"])] + origem["apelidos"]:
            if a not in destino["apelidos"]:
                destino["apelidos"].append(a)
        destino["atualizado"] = time.time()
        self.monstros.remove(origem)
        self.salvar()

    def remover(self, m):
        if m.get("pet_id"):
            self.pets_ocultos[str(m["pet_id"])] = m["qtd"]
        self.monstros.remove(m)
        self.salvar()

    def zerar_tudo(self):
        for m in self.monstros:
            m["qtd"] = 0
        self.salvar()

    def remover_todos(self):
        """Tira todos os monstros da lista. Guarda uma copia antes
        (progresso_antes_de_limpar.json), caso tenha sido sem querer."""
        try:
            salvar_json(self.arquivo.replace(".json", "_antes_de_limpar.json"),
                        {"monstros": self.monstros})
        except Exception:
            pass
        n = len(self.monstros)
        self.monstros.clear()
        self.salvar()
        return n


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------
class OcrWindows:
    """OCR nativo do Windows 10/11 (nao precisa instalar nada extra)."""
    nome = "OCR do Windows"

    def __init__(self, idioma):
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import SoftwareBitmap, BitmapPixelFormat
        from winrt.windows.storage.streams import DataWriter
        self._SB, self._PF, self._DW = SoftwareBitmap, BitmapPixelFormat, DataWriter
        motor = None
        try:
            lang = Language(idioma)
            if OcrEngine.is_language_supported(lang):
                motor = OcrEngine.try_create_from_language(lang)
        except Exception:
            motor = None
        if motor is None:
            motor = OcrEngine.try_create_from_user_profile_languages()
        if motor is None:
            raise RuntimeError("Nenhum idioma de OCR instalado no Windows.")
        self.motor = motor
        try:
            self.nome = f"OCR do Windows ({motor.recognizer_language.language_tag})"
        except Exception:
            pass
        self.loop = asyncio.new_event_loop()

    async def _reconhecer(self, bmp):
        return await self.motor.recognize_async(bmp)

    def ler(self, img):
        from PIL import Image
        img = img.convert("RGBA")
        r, g, b, a = img.split()
        dados = Image.merge("RGBA", (b, g, r, a)).tobytes()
        w = self._DW()
        try:
            w.write_bytes(dados)
        except TypeError:
            w.write_bytes(list(dados))
        bmp = self._SB.create_copy_from_buffer(
            w.detach_buffer(), self._PF.BGRA8, img.width, img.height)
        res = self.loop.run_until_complete(self._reconhecer(bmp))
        self._ultimo_res = res
        return [l.text for l in res.lines]

    def linhas_pos(self, img):
        self.ler(img)
        out = []
        for l in self._ultimo_res.lines:
            ws = list(l.words)
            if not ws:
                continue
            ys = sorted(w.bounding_rect.y + w.bounding_rect.height / 2 for w in ws)
            hs = sorted(w.bounding_rect.height for w in ws)
            out.append((l.text, ys[len(ys) // 2], hs[len(hs) // 2]))
        out.sort(key=lambda x: x[1])
        return out

    def palavras(self, img):
        """Lista de (texto, x, y, largura, altura) de cada palavra."""
        self.ler(img)
        out = []
        for l in self._ultimo_res.lines:
            for w in l.words:
                r = w.bounding_rect
                out.append((w.text, int(r.x), int(r.y), int(r.width), int(r.height)))
        return out


class OcrTesseract:
    nome = "Tesseract"

    def __init__(self, idioma):
        import pytesseract
        import shutil
        candidatos = [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                      os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                                   "Tesseract-OCR", "tesseract.exe")]
        achado = next((c for c in candidatos if c and os.path.exists(c)), None) \
            or shutil.which("tesseract")
        if achado:
            pytesseract.pytesseract.tesseract_cmd = achado
        self.pt = pytesseract
        # nao tentar ler texto invertido: ~40% mais rapido, mesmo resultado
        self.cfg_rapida = "-c tessedit_do_invert=0"
        self.paralelo = True  # cada leitura e um processo separado
        # Pacote de portugues que o instalador baixa para a pasta do programa.
        # A pasta vai pela variavel TESSDATA_PREFIX (e nao por --tessdata-dir):
        # no Windows o pytesseract passaria o caminho COM as aspas e o
        # Tesseract nao acha o arquivo ("Error opening data file").
        pasta_td = os.path.join(BASE, "tessdata")
        arq_por = os.path.join(pasta_td, "por.traineddata")
        usou_pasta = False
        if os.path.exists(arq_por) and os.path.getsize(arq_por) > 500_000:
            os.environ["TESSDATA_PREFIX"] = pasta_td
            usou_pasta = True
        try:
            self._escolher_idioma()
            self._testar()
        except Exception as e:
            if not usou_pasta:
                raise
            # o pacote da pasta deu problema: volta para o do proprio Tesseract
            registrar(f"AVISO: pacote de portugues da pasta falhou ({e}); usando o do Tesseract")
            os.environ.pop("TESSDATA_PREFIX", None)
            self._escolher_idioma()
            self._testar()
        origem = "pasta do programa" if os.environ.get("TESSDATA_PREFIX") else "Tesseract"
        self.nome = f"Tesseract ({self.lang}, {origem})"

    def _escolher_idioma(self):
        disp = self.pt.get_languages(config="")
        # um idioma so e bem mais rapido; o jogo esta em portugues
        quer = [l for l in ("por", "eng") if l in disp][:1] or disp[:1]
        if not quer:
            raise RuntimeError("Tesseract sem nenhum idioma instalado")
        self.lang = quer[0]

    def _testar(self):
        """Le uma imagem pequena na abertura: se o Tesseract nao funcionar, o
        erro aparece agora (com a mensagem certa) e nao a cada leitura."""
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (220, 40), "white")
        ImageDraw.Draw(img).text((8, 12), "Obteve 123", fill="black")
        self.pt.image_to_string(img, lang=self.lang, config="--psm 7 " + self.cfg_rapida)

    def ler(self, img):
        from PIL import ImageOps, ImageStat
        g = ImageOps.grayscale(img)
        if ImageStat.Stat(g).mean[0] < 128:  # texto claro em fundo escuro
            g = ImageOps.invert(g)
        txt = self.pt.image_to_string(g, lang=self.lang, config="--psm 6 " + self.cfg_rapida)
        return [l for l in txt.splitlines() if l.strip()]

    def linhas_pos(self, img):
        """[(texto, y_centro, altura)] lendo so as faixas da imagem com texto."""
        faixas = faixas_de_texto(img)
        if not faixas:
            return []
        from PIL import Image
        gap = 12
        alt_total = sum(b - a for a, b in faixas) + gap * (len(faixas) + 1)
        junto = Image.new("RGB", (img.width, alt_total), "white")
        mapa = []  # (y_no_junto_inicio, y_no_junto_fim, deslocamento)
        y = gap
        for a, b in faixas:
            junto.paste(img.crop((0, a, img.width, b)), (0, y))
            mapa.append((y, y + (b - a), a - y))
            y += (b - a) + gap
        d = self.pt.image_to_data(junto, lang=self.lang, config="--psm 6 " + self.cfg_rapida,
                                  output_type=self.pt.Output.DICT)
        grupos = {}
        for i, t in enumerate(d["text"]):
            if not t.strip():
                continue
            chave = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
            grupos.setdefault(chave, []).append(
                (d["left"][i], t, d["top"][i], d["height"][i]))
        out = []
        for ws in grupos.values():
            ws.sort()
            texto = " ".join(w[1] for w in ws)
            cy = sorted(w[2] + w[3] / 2 for w in ws)[len(ws) // 2]
            h = sorted(w[3] for w in ws)[len(ws) // 2]
            desl = next((m[2] for m in mapa if m[0] - gap / 2 <= cy <= m[1] + gap / 2), 0)
            out.append((texto, cy + desl, h))
        out.sort(key=lambda x: x[1])
        return out

    def palavras(self, img):
        d = self.pt.image_to_data(img, lang=self.lang, config="--psm 11 " + self.cfg_rapida,
                                  output_type=self.pt.Output.DICT)
        return [(d["text"][i], d["left"][i], d["top"][i], d["width"][i], d["height"][i])
                for i in range(len(d["text"])) if d["text"][i].strip()]


def faixas_de_texto(img, min_alt=8):
    """Acha as faixas horizontais da imagem tratada (texto preto em fundo
    branco) que tem letras. Usa a media de cada linha de pixels: rapido."""
    L = img.convert("L")
    perfil = _perfil_linhas(L)
    faixas, ini = [], None
    for y, v in enumerate(perfil):
        tem = v > 0.004  # fracao de pixels pretos na linha
        if tem and ini is None:
            ini = y
        elif not tem and ini is not None:
            faixas.append([ini, y])
            ini = None
    if ini is not None:
        faixas.append([ini, len(perfil)])
    # junta pedacos da mesma linha de texto (acentos, pontos)
    juntas = []
    for a, b in faixas:
        if juntas and a - juntas[-1][1] <= 3:
            juntas[-1][1] = b
        else:
            juntas.append([a, b])
    out = []
    for a, b in juntas:
        if b - a >= min_alt:
            out.append((max(0, a - 3), min(L.height, b + 3)))
    return out


def _perfil_linhas(L):
    """Fracao de pixels escuros em cada linha da imagem."""
    from PIL import Image
    bw = L.point(lambda v: 255 if v < 128 else 0)
    # reduzir so a largura para 1 pixel faz a media de cada linha (BOX)
    col = bw.resize((1, bw.height), Image.BOX)
    return [v / 255.0 for v in col.getdata()]


def _parece(palavra, alvo, minimo=0.8):
    s = re.sub(r"[^a-z]", "", sem_acento(palavra).lower())
    if len(s) < len(alvo) - 1:
        return False
    n = len(alvo)
    return any(difflib.SequenceMatcher(None, s[j:j + n], alvo).ratio() >= minimo
               for j in range(max(1, len(s) - n + 2)))


def apagar_proprias(img, reg, areas):
    """Pinta de preto, na imagem capturada, o pedaco onde estao as janelas do
    proprio programa (overlay, janela de leitura). Assim o overlay pode
    aparecer na stream sem o OCR ler o que esta escrito nele."""
    if not areas:
        return img
    from PIL import ImageDraw
    img = img.copy()
    d = ImageDraw.Draw(img)
    for x, y, w, h in areas:
        x0, y0 = x - reg["left"], y - reg["top"]
        x1, y1 = x0 + w, y0 + h
        if x1 <= 0 or y1 <= 0 or x0 >= img.width or y0 >= img.height:
            continue
        d.rectangle([max(0, x0 - 4), max(0, y0 - 4), min(img.width, x1 + 4), min(img.height, y1 + 4)],
                    fill=(0, 0, 0))
    return img


def achar_chat(motor, cfg, sct, avisar=lambda t: None, proprias=()):
    """Procura o chat do jogo sozinho: em cada monitor, olha o canto inferior
    esquerdo atras de palavras 'obteve' / 'Pontos da Alma' e monta a area em
    volta delas. Devolve a regiao (coordenadas da captura) ou None."""
    from PIL import Image
    melhor = None
    monitores = sct.monitors[1:] or sct.monitors[:1]
    for n, mon in enumerate(monitores, 1):
        avisar(f"Procurando o chat no monitor {n} de {len(monitores)}…")
        W, H = mon["width"], mon["height"]
        area = {"left": mon["left"], "top": mon["top"] + int(H * 0.3),
                "width": int(W * 0.5), "height": H - int(H * 0.3)}
        shot = sct.grab(area)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        img = apagar_proprias(img, area, proprias)
        c2 = dict(cfg)
        c2["escala_ocr"] = 1.5
        esc = 1.5
        trat = preparar_imagem(img, c2)
        acertos = []
        for txt, x, y, w, h in motor.palavras(trat):
            if _parece(txt, "obteve") or _parece(txt, "pontos") or _parece(txt, "kinah"):
                acertos.append((area["left"] + x / esc, area["top"] + y / esc,
                                area["left"] + (x + w) / esc, area["top"] + (y + h) / esc))
        if len(acertos) >= 2 and (melhor is None or len(acertos) > len(melhor[0])):
            melhor = (acertos, mon)
    if not melhor:
        return None
    acertos, mon = melhor
    xs = [a[0] for a in acertos]
    ys = [a[1] for a in acertos]
    ye = [a[3] for a in acertos]
    alt = sorted(a[3] - a[1] for a in acertos)[len(acertos) // 2] or 14
    # o "Voce" antes do "obteve" e o "x1." que quebra de linha ficam bem na
    # borda esquerda do chat: sobra bastante para a esquerda
    esquerda = max(mon["left"], int(min(xs) - alt * 7))
    largura = int(mon["width"] * 0.19 + alt * 5)
    topo = max(mon["top"], int(min(ys) - alt * 3))
    base = min(mon["top"] + mon["height"], int(max(ye) + alt * 2))
    return {"left": esquerda, "top": topo, "width": largura, "height": base - topo}


def criar_motor(cfg):
    escolha = cfg.get("motor_ocr", "auto")
    erros = []
    ordem = {"windows": [OcrWindows], "tesseract": [OcrTesseract]}.get(
        escolha, [OcrWindows, OcrTesseract])
    for cls in ordem:
        try:
            return cls(cfg.get("idioma", "pt-BR"))
        except Exception as e:
            erros.append(f"{cls.nome}: {e}")
    raise RuntimeError("Nao consegui iniciar o OCR.\n" + "\n".join(erros))


def preparar_imagem(img, cfg):
    """Amplia e, se ligado, deixa so as letras do jogo (claras com contorno
    escuro) em preto sobre branco. Remove flores/ceu claros atras do chat."""
    from PIL import Image, ImageChops, ImageFilter
    esc = float(cfg.get("escala_ocr", 3.0))
    if esc != 1.0:
        img = img.resize((int(img.width * esc), int(img.height * esc)), Image.LANCZOS)
    if not cfg.get("filtro_contorno", True):
        return img
    L = img.convert("L")
    k = 2 * max(1, int(round(esc))) + 1
    escuro_perto = L.filter(ImageFilter.MinFilter(k))
    lc, le = int(cfg.get("limiar_claro", 150)), int(cfg.get("limiar_escuro", 90))
    claro = L.point(lambda v: 255 if v > lc else 0)
    # nomes de item coloridos (azul/verde/roxo do OD Puro, OD Fino...) tem
    # brilho baixo na conta do cinza, mas um canal de cor bem forte
    r, g, b = img.convert("RGB").split()
    forte = ImageChops.lighter(ImageChops.lighter(r, g), b).point(
        lambda v: 255 if v > int(cfg.get("limiar_cor", 205)) else 0)
    claro = ImageChops.lighter(claro, forte)
    contorno = escuro_perto.point(lambda v: 255 if v < le else 0)
    letras = ImageChops.multiply(claro, contorno)
    return ImageChops.invert(letras).convert("RGB")


class _Foto:
    """Mesmo formato do resultado do mss (size + bgra)."""
    def __init__(self, w, h, bgra):
        self.size = (w, h)
        self.bgra = bgra


class CapturaDirectX:
    """Captura pela Duplicacao de Area de Trabalho do DirectX (DXGI). Pega a
    imagem ja composta que vai para o monitor, com todas as camadas. Serve
    quando a captura normal mostra o jogo mas nao mostra o chat (tela cheia,
    HDR, 'Otimizacoes para jogos em janela' do Windows 11...)."""

    def __init__(self, sct_normal):
        import dxcam
        self.normal = sct_normal
        self.monitors = sct_normal.monitors
        self.saidas = []   # (left, top, right, bottom, camera)
        self.ultimo = {}
        for dev in range(4):
            for out in range(8):
                try:
                    cam = dxcam.create(device_idx=dev, output_idx=out, output_color="BGRA")
                except Exception:
                    continue
                if cam is None:
                    continue
                ret = self._retangulo(cam)
                if ret:
                    self.saidas.append(ret + (cam,))
        if not self.saidas:
            raise RuntimeError("DirectX nao encontrou nenhum monitor")

    def _retangulo(self, cam):
        try:
            r = cam._output.desc.DesktopCoordinates
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            pass
        try:  # sem posicao: casa pelo tamanho com os monitores do mss
            w, h = cam.width, cam.height
            for m in self.monitors[1:]:
                if m["width"] == w and m["height"] == h:
                    return (m["left"], m["top"], m["left"] + w, m["top"] + h)
        except Exception:
            pass
        return None

    def grab(self, reg):
        cx = reg["left"] + reg["width"] // 2
        cy = reg["top"] + reg["height"] // 2
        for l, t, r, b, cam in self.saidas:
            if l <= cx < r and t <= cy < b:
                x0 = max(0, reg["left"] - l)
                y0 = max(0, reg["top"] - t)
                x1 = min(r - l, reg["left"] + reg["width"] - l)
                y1 = min(b - t, reg["top"] + reg["height"] - t)
                chave = (x0, y0, x1, y1)
                try:
                    quadro = cam.grab(region=chave)
                except Exception:
                    quadro = None
                if quadro is None:
                    # DirectX so entrega quadro quando a tela muda: repete o ultimo
                    if chave in self.ultimo:
                        return self.ultimo[chave]
                    break
                foto = _Foto(x1 - x0, y1 - y0, quadro.tobytes())
                self.ultimo[chave] = foto
                return foto
        return self.normal.grab(reg)


def nova_captura(cfg, sct_normal, avisar=lambda t: None):
    if cfg.get("captura", "normal") == "directx":
        try:
            cap = CapturaDirectX(sct_normal)
            avisar("CAPTURA: DirectX")
            return cap
        except Exception as e:
            avisar(f"CAPTURA: DirectX falhou ({e}); usando a normal")
    return sct_normal


class _ExecutorDireto:
    """Mesmo jeito de usar do ThreadPoolExecutor, mas roda na hora."""
    def submit(self, fn, *a):
        from concurrent.futures import Future
        f = Future()
        try:
            f.set_result(fn(*a))
        except Exception as e:
            f.set_exception(e)
        return f

    def shutdown(self, wait=False):
        pass


class Leitor(threading.Thread):
    """Thread que captura a area escolhida e faz o OCR."""

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.fila = queue.Queue()
        self.ativo = threading.Event()
        self.ativo.set()
        self.parar = threading.Event()
        self.ultima = None  # (imagem capturada, imagem tratada)
        self.pedir_deteccao = threading.Event()
        self.areas_proprias = []  # janelas do programa (para nao ler a si mesmo)

    def run(self):
        if sys.platform == "win32":
            try:
                ctypes.windll.combase.RoInitialize(1)  # MTA
            except Exception:
                pass
        try:
            motor = criar_motor(self.cfg)
            self.fila.put(("motor", motor.nome))
        except Exception as e:
            self.fila.put(("erro", str(e)))
            return
        try:
            import mss
            from PIL import Image
        except Exception as e:
            self.fila.put(("erro", f"Faltam bibliotecas: {e}"))
            return
        from concurrent.futures import ThreadPoolExecutor
        n_workers = 1
        if getattr(motor, "paralelo", False):
            n_workers = max(1, int(self.cfg.get("leituras_paralelas", 2)))
        if getattr(motor, "paralelo", False):
            pool = ThreadPoolExecutor(max_workers=n_workers)
        else:
            pool = _ExecutorDireto()  # OCR do Windows: roda na propria thread
        pendentes = deque()
        with abrir_mss(mss) as sct_normal:
            modo_cap = self.cfg.get("captura", "normal")
            sct = nova_captura(self.cfg, sct_normal, lambda t: self.fila.put(("aviso", t)))
            if not self.cfg.get("regiao"):
                self.pedir_deteccao.set()
            while not self.parar.is_set():
                if self.cfg.get("captura", "normal") != modo_cap:
                    modo_cap = self.cfg.get("captura", "normal")
                    sct = nova_captura(self.cfg, sct_normal, lambda t: self.fila.put(("aviso", t)))
                    self._mini = None
                if self.pedir_deteccao.is_set():
                    self.pedir_deteccao.clear()
                    while pendentes:
                        self._esvaziar(pendentes, esperar=True)
                    try:
                        reg = achar_chat(motor, self.cfg, sct,
                                         lambda t: self.fila.put(("status", t)),
                                         list(self.areas_proprias))
                    except Exception as e:
                        reg = None
                        self.fila.put(("aviso", f"Falha ao procurar o chat: {e}"))
                    self.fila.put(("regiao", reg))
                    if not reg:
                        time.sleep(5)  # tenta de novo daqui a pouco
                        if not self.cfg.get("regiao"):
                            self.pedir_deteccao.set()
                    continue
                reg = self.cfg.get("regiao")
                if not self.ativo.is_set() or not reg:
                    self._esvaziar(pendentes, esperar=True)
                    time.sleep(0.25)
                    continue
                t0 = time.time()
                try:
                    shot = sct.grab(reg)
                    bruta = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    bruta = apagar_proprias(bruta, reg, self.areas_proprias)
                    # se a tela nao mudou, nao gasta OCR de novo
                    mini = bruta.convert("L").resize((48, max(1, bruta.height * 48 // bruta.width)))
                    if self._igual(mini):
                        self.fila.put(("repetido", None))
                    else:
                        self._mini = mini
                        fut = pool.submit(self._processar, motor, bruta, t0)
                        pendentes.append(fut)
                except Exception as e:
                    self.fila.put(("aviso", f"Falha na captura: {e}"))
                    time.sleep(1.0)
                # entrega os resultados prontos, sempre na ordem das capturas
                self._esvaziar(pendentes, esperar=len(pendentes) >= n_workers)
                espera = float(self.cfg.get("intervalo", 0.3)) - (time.time() - t0)
                if espera > 0:
                    time.sleep(espera)
            pool.shutdown(wait=False)

    def _igual(self, mini):
        ant = getattr(self, "_mini", None)
        if ant is None or ant.size != mini.size:
            return False
        from PIL import ImageChops, ImageStat
        dif = ImageStat.Stat(ImageChops.difference(ant, mini)).mean[0]
        return dif < 1.5

    def _processar(self, motor, bruta, t0):
        img = preparar_imagem(bruta, self.cfg)
        self.ultima = (bruta, img)
        if hasattr(motor, "linhas_pos"):
            return ("linhas_pos", motor.linhas_pos(img), t0)
        return ("linhas", motor.ler(img), t0)

    def _esvaziar(self, pendentes, esperar=False):
        while pendentes and (pendentes[0].done() or esperar):
            fut = pendentes.popleft()
            esperar = False
            try:
                tipo, dado, t0 = fut.result()
                self.fila.put((tipo, (dado, t0)))
            except Exception as e:
                self.fila.put(("aviso", f"Falha na leitura: {e}"))


class LeitorRede(threading.Thread):
    """Entrega ganhos de alma do Npcap na mesma fila usada pela interface."""

    def __init__(self, cfg):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.fila = queue.Queue()
        self.ativo = threading.Event()
        self.ativo.set()
        self.parar = threading.Event()
        self.areas_proprias = []
        self.ultima = None
        try:
            with open(os.path.join(BASE, "pet_nomes_npcap.json"), encoding="utf-8") as f:
                self.nomes = json.load(f)
        except (OSError, ValueError):
            self.nomes = {}

    def run(self):
        try:
            import pet_protocol
            if not pet_protocol.npcap_installed():
                raise RuntimeError("Npcap não está instalado. Instale pelo site npcap.com e abra o contador de novo.")
            if not self.nomes:
                raise RuntimeError("Falta a tabela pet_nomes_npcap.json. Atualize ou reinstale o contador.")
            self.fila.put(("motor", "Npcap"))

            def receber(evento):
                if evento[0] == "gain" and self.ativo.is_set():
                    _, pet_id, quantidade = evento
                    self.fila.put(("alma_rede", (pet_id, quantidade)))
                elif evento[0] in ("abyss_points", "kinah_gain", "od_gain", "gather_gain") and self.ativo.is_set():
                    if evento[0] in ("od_gain", "gather_gain"):
                        self.fila.put((evento[0], (evento[1], evento[2])))
                    else:
                        self.fila.put(evento)
                elif evento[0] == "conn":
                    self.fila.put(("conexao_rede", evento[1]))
                elif evento[0] == "ready":
                    self.fila.put(("rede_pronta", True))
                elif evento[0] == "state" and self.ativo.is_set():
                    self.fila.put(("estado_rede", evento[1:]))
                elif evento[0] == "nickname":
                    self.fila.put(("personagem_rede", evento[1]))
                elif evento[0] == "dps_party":
                    self.fila.put(("dps_party", evento[1]))
                elif evento[0] == "dps_damage" and self.ativo.is_set():
                    self.fila.put(("dps_damage", (evento[1], evento[2])))
                elif evento[0] == "dps_reset":
                    self.fila.put(("dps_reset", None))
                elif evento[0] == "boss_health":
                    self.fila.put(("boss_health", (evento[1], evento[2])))

            pet_protocol.run_sniffer(receber, self.parar)
        except Exception as e:
            if not self.parar.is_set():
                self.fila.put(("erro", str(e)))


# --------------------------------------------------------------------------
# Interface (overlay)
# --------------------------------------------------------------------------
COR = {
    "fundo": "#12141a",
    "painel": "#1b1e26",
    "destaque": "#24403c",
    "texto": "#ebe8e3",
    "suave": "#8a8f9b",
    "barra": "#2b2f3a",
    "alma": "#5fd4c4",
    "ouro": "#e6c15a",
    "erro": "#e66a5a",
    "novo": "#ffe27a",  # monstro que acabou de ganhar alma
}
MATERIAL_CORES = {
    "Ore": "#e89a62",        # cobre suave
    "Gem": "#ad8cf7",        # ametista
    "Log": "#c98a65",        # madeira quente
    "Herb": "#72c99a",       # verde sálvia
    "Ingredient": "#e77f9f", # rosa framboesa
}

# cor de destaque escolhida no menu (titulo, numeros, barras, som)
CORES_DESTAQUE = [
    ("Verde-\u00e1gua", "#5fd4c4"), ("Rosa", "#ff7eb6"), ("Roxo", "#b48cff"),
    ("Azul", "#6aa8ff"), ("Dourado", "#f2c94c"), ("Laranja", "#ff9f5a"),
    ("Vermelho", "#ff6b6b"), ("Verde", "#7ddc7a"),
    ("Lavanda", "#c5a3ff"), ("Lil\u00e1s", "#d98cff"), ("Coral", "#ff8a7a"),
    ("Salm\u00e3o", "#f2a08f"), ("Ciano", "#62d6ff"), ("Menta", "#8de0b2"),
    ("P\u00eassego", "#ffc080"), ("P\u00e9rola", "#eadfcf"),
]


def aplicar_cor_destaque(cor):
    COR["alma"] = cor
    # alma nova pisca em dourado; se o destaque ja e dourado, pisca em branco
    COR["novo"] = "#ffffff" if cor.lower() in ("#f2c94c", "#e6c15a") else "#ffe27a"


def main():
    if "--esperar" in sys.argv:
        try:
            esperar_processo(sys.argv[sys.argv.index("--esperar") + 1])
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    import tkinter as tk
    from tkinter import simpledialog, messagebox

    class App:
        def __init__(self):
            self.cfg = carregar_config()
            self.cfg["recolhido"] = False
            # A leitura do jogo é passiva pelo Npcap; o chat não é fonte de dados.
            self.cfg["fonte_leitura"] = "npcap"
            # O nome salvo pode pertencer ao personagem anterior; só exibimos
            # um nome depois que o Npcap identificar a sessão atual.
            self.personagem = ""
            self._personagem_candidato = self.cfg.get("personagem", "")
            self._assinatura_personagem_candidato = self.cfg.get("personagem_estado")
            self._assinatura_estado_atual = None
            self.sincronizacao_pendente = False
            VOLUME_AVISO["v"] = float(self.cfg.get("volume_aviso", 0.5))
            aplicar_cor_destaque(self.cfg.get("cor_destaque", "#5fd4c4"))
            self.cfg["aba"] = "almas"  # sempre abre nas almas (mede a largura certa)
            self.prog = Progresso(ARQ_DADOS)
            self.nomes_pets = carregar_json(os.path.join(BASE, "pet_nomes_npcap.json"), {})
            self.rastreador = Rastreador(self.cfg["padroes"], self.prog.canonico, self.prog.completar,
                                         self.prog.comeco_de_outro)
            self.destaque = {}
            self.pontos_abismo = 0  # ganho desde que o contador foi aberto
            self.kinah_abismo = 0
            self.dps_party = []
            self.dps_damage = {}
            self.dps_started = None
            self.dps_last_hit = None
            self.boss_health = (0, 0)
            self.motor_nome = "iniciando Npcap..."
            self.rede_conectada = False
            self.rede_pronta = False
            self.rede_sincronizada = False
            self.erro = None
            self.debug = None
            self.ultimas_linhas = []

            if sys.platform == "win32":
                # identidade propria na barra de tarefas (senao o Windows agrupa
                # com o Python e mostra o icone dele)
                try:
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                        "vitoriaAngelo.ContadorDeAlmas")
                except Exception:
                    pass
            self.root = tk.Tk()
            self.root.title(APP_NOME)
            try:
                ico = os.path.join(BASE, "icone.ico")
                if os.path.exists(ico):
                    self.root.iconbitmap(default=ico)
            except Exception:
                pass
            self.k = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.root.attributes("-alpha", float(self.cfg["opacidade"]))
            self.root.configure(bg=COR["fundo"], highlightthickness=1,
                                highlightbackground="#2e3340")
            x, y = self.cfg.get("posicao", [40, 200])
            self.root.geometry(f"+{int(x)}+{int(y)}")

            # fontes com nome: mudar o tamanho no menu atualiza tudo na hora
            import tkinter.font as tkfont
            self._fontes_base = {
                "titulo": ("Segoe UI", 10, "bold"), "nome": ("Segoe UI", 9, "bold"),
                "peq": ("Segoe UI", 8, "normal"), "estrela": ("Segoe UI", 10, "normal"),
                "botao": ("Segoe UI", 10, "normal"), "botao_b": ("Segoe UI", 10, "bold"),
                "fechar": ("Segoe UI", 9, "bold"), "relogio": ("Consolas", 9, "bold"),
                "evento": ("Segoe UI", 8, "bold"), "versao": ("Segoe UI", 7, "normal"),
            }
            self.fontes = {k: tkfont.Font(root=self.root, family=f, size=t, weight=p)
                           for k, (f, t, p) in self._fontes_base.items()}
            self._aplicar_tamanho_fontes()
            self.f_titulo = self.fontes["titulo"]
            self.f_nome = self.fontes["nome"]
            self.f_peq = self.fontes["peq"]

            self._montar_cabecalho()
            self.area_abas = tk.Frame(self.root, bg=COR["fundo"])
            self.area_abas.pack(fill="both", expand=True, padx=self.px(8), pady=(0, self.px(8)))
            self._abas_visuais = {}
            self.corpo = tk.Frame(self.area_abas, bg=COR["fundo"])
            self.corpo.pack(fill="both", expand=True)
            self.render()

            canto = tk.Canvas(self.root, width=self.px(14), height=self.px(14),
                              bg=COR["fundo"], highlightthickness=0, cursor="size_nw_se")
            for inicio in (4, 8):
                canto.create_line(self.px(inicio), self.px(12), self.px(12),
                                  self.px(inicio), fill=COR["suave"])
            canto.place(relx=1, rely=1, anchor="se")
            canto.bind("<ButtonPress-1>", self._tamanho_ini)
            canto.bind("<B1-Motion>", self._tamanho_arrastar)
            canto.bind("<ButtonRelease-1>", self._tamanho_salvar)
            self.dica(canto, "Arraste para ajustar o tamanho da janela")
            tamanho = self.cfg.get("tamanho_janela")
            if isinstance(tamanho, list) and len(tamanho) == 2:
                largura = max(self.px(320), min(int(tamanho[0]), self.root.winfo_screenwidth()))
                altura = max(self.px(180), min(int(tamanho[1]), self.root.winfo_screenheight()))
                self.root.geometry(f"{largura}x{altura}")

            self.leitor = LeitorRede(self.cfg)
            self.leitor_ocr = None
            self.erro_od = None
            self.motor_ocr_nome = "iniciando leitura do chat"
            self.leitor.start()
            self.root.after(100, self.consumir)
            self.root.after(3000, self.reforcar_topo)
            self.root.protocol("WM_DELETE_WINDOW", self.sair)

            self.status_extra = None
            self.root.after(300, lambda: self.esconder_da_captura(self.root))
            self.root.after(200, self.virar_janela_normal)
            self.root.after(100, self.atualizar_areas_proprias)
            self.root.after(500, self.atualizar_evento)
            self.root.after(4000, self.checar_atualizacao_em_segundo_plano)
            self.root.after(20000, self.vigiar_arquivo)

        # ---------------- util ----------------
        def esconder_da_captura(self, janela):
            """Deixa a janela do programa invisivel para capturas de tela, para
            o OCR nunca ler o proprio overlay (Windows 10 2004+)."""
            if sys.platform != "win32":
                return
            modo = 0x11 if self.cfg.get("esconder_da_captura", False) else 0x0
            try:
                janela.update_idletasks()
                u32 = ctypes.windll.user32
                hwnd = janela.winfo_id()
                pai = u32.GetParent(hwnd)
                for h in (hwnd, pai):
                    if h:
                        u32.SetWindowDisplayAffinity(h, modo)  # 0x11 = some da captura
            except Exception:
                pass

        def virar_janela_normal(self):
            """Janela sem borda vira 'janela de ferramenta' no Windows, e o OBS
            esconde esse tipo da lista da Captura de Janela. Aqui ela passa a ser
            uma janela normal (aparece no OBS como 'Contador de Almas')."""
            if sys.platform != "win32":
                return
            try:
                u32 = ctypes.windll.user32
                GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x80, 0x40000
                hwnd = u32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                pegar = getattr(u32, "GetWindowLongPtrW", u32.GetWindowLongW)
                por = getattr(u32, "SetWindowLongPtrW", u32.SetWindowLongW)
                pegar.restype = ctypes.c_ssize_t
                pegar.argtypes = [ctypes.c_void_p, ctypes.c_int]
                por.restype = ctypes.c_ssize_t
                por.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
                estilo = pegar(hwnd, GWL_EXSTYLE)
                novo = (estilo & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
                if novo != estilo:
                    por(hwnd, GWL_EXSTYLE, novo)
                    u32.SetWindowTextW(hwnd, APP_NOME)
                    # o Windows so aplica depois de esconder e mostrar de novo
                    self.root.withdraw()
                    self.root.after(50, self._reaparecer)
            except Exception as e:
                registrar(f"AVISO janela normal: {e}")

        def _reaparecer(self):
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.esconder_da_captura(self.root)

        def pegar_teclado(self):
            """Overlay sem borda nao recebe teclado sozinho no Windows: traz a
            janela para frente para a caixinha poder ser digitada."""
            if sys.platform != "win32":
                return
            try:
                u32 = ctypes.windll.user32
                hwnd = u32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                u32.SetForegroundWindow(hwnd)
            except Exception:
                pass

        def alternar_stream(self):
            self.cfg["esconder_da_captura"] = not self.cfg.get("esconder_da_captura", False)
            self.salvar_cfg()
            self.esconder_da_captura(self.root)
            if self.debug and self.debug.winfo_exists():
                self.esconder_da_captura(self.debug)

        def atualizar_areas_proprias(self):
            """Informa ao leitor onde estao as janelas do programa, para ele
            apagar essa parte da imagem antes de ler (nunca le a si mesmo)."""
            areas = []
            for j in (self.root, self.debug):
                try:
                    if j is not None and j.winfo_exists() and j.winfo_viewable():
                        areas.append((j.winfo_rootx(), j.winfo_rooty(),
                                      j.winfo_width(), j.winfo_height()))
                except Exception:
                    pass
            self.leitor.areas_proprias = areas
            if self.leitor_ocr:
                self.leitor_ocr.areas_proprias = areas
            self.root.after(300, self.atualizar_areas_proprias)

        def detectar_chat(self):
            self.erro_od = None
            self.status_extra = "procurando o chat…"
            self.atualizar_status()
            leitor = self.leitor_ocr or self.leitor
            if hasattr(leitor, "pedir_deteccao"):
                leitor.pedir_deteccao.set()

        def leitor_imagem(self):
            return self.leitor_ocr or self.leitor
        def px(self, v):
            return int(round(v * self.k))

        def salvar_cfg(self):
            try:
                salvar_json(ARQ_CONFIG, self.cfg)
            except Exception:
                pass

        def abrir_menu(self, menu, e):
            """Abre o menu na frente do overlay. O overlay fica 'sempre por
            cima' e passava na frente do menu: tira isso enquanto o menu esta
            aberto e devolve quando ele fecha."""
            self._menu_aberto = True
            try:
                self.root.attributes("-topmost", False)
            except Exception:
                pass

            def devolver():
                try:
                    if menu.winfo_exists() and menu.winfo_ismapped():
                        self.root.after(200, devolver)  # ainda aberto
                        return
                except Exception:
                    pass
                self._menu_aberto = False
                try:
                    if not getattr(self, "minimizado", False):
                        self.root.attributes("-topmost", True)
                except Exception:
                    pass
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                try:
                    menu.grab_release()
                except Exception:
                    pass
                self.root.after(150, devolver)

        def reforcar_topo(self):
            try:
                # minimizado fica minimizado: nao traz de volta para cima
                if getattr(self, "_menu_aberto", False):
                    pass  # menu aberto: nao passa na frente dele
                elif not getattr(self, "minimizado", False) and self.root.state() != "iconic":
                    self.root.attributes("-topmost", True)
                    self.root.lift()
                    dica = getattr(self, "_dica_aberta", None)
                    if dica is not None:
                        try:
                            dica.lift()  # a dica fica por cima do contador
                        except Exception:
                            self._dica_aberta = None
            except Exception:
                pass
            self.root.after(3000, self.reforcar_topo)

        def minimizar(self):
            """Manda o overlay para a barra de tarefas. Ele continua contando e so
            volta quando voce clica nele na barra de tarefas."""
            if getattr(self, "minimizado", False):
                return
            self.minimizado = True
            self.cfg["posicao"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.salvar_cfg()
            # janela sem borda nao pode ser minimizada no Windows: tira a
            # "falta de borda" so enquanto estiver minimizada
            r = self.root
            r.attributes("-topmost", False)
            r.withdraw()
            r.overrideredirect(False)
            r.update_idletasks()
            r.deiconify()

            def baixar():
                r.iconify()
                # so comeca a vigiar a volta depois que ja minimizou
                r.after(300, lambda: r.bind("<Map>", self._ao_restaurar))
            r.after(60, baixar)

        def _ao_restaurar(self, e=None):
            if not getattr(self, "minimizado", False) or e is None or e.widget is not self.root:
                return
            if self.root.state() == "iconic":
                return
            self.minimizado = False
            self.root.unbind("<Map>")
            self.root.after(10, self._voltar_ao_normal)

        def _voltar_ao_normal(self):
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.update_idletasks()
            self.root.deiconify()
            x, y = self.cfg.get("posicao", [40, 200])
            self.root.geometry(f"+{int(x)}+{int(y)}")
            self.root.attributes("-topmost", True)
            self.root.lift()
            self.esconder_da_captura(self.root)
            self.virar_janela_normal()

        def dialogo(self, fn, *a, **kw):
            self.root.attributes("-topmost", False)
            try:
                return fn(*a, parent=self.root, **kw)
            finally:
                self.root.attributes("-topmost", True)

        def dica(self, w, texto):
            """Dica que aparece ao parar o mouse em cima (texto ou funcao)."""
            estado = {"id": None, "jan": None}

            def esconder(e=None):
                if estado["id"]:
                    try:
                        self.root.after_cancel(estado["id"])
                    except Exception:
                        pass
                    estado["id"] = None
                if estado["jan"] is not None:
                    try:
                        estado["jan"].destroy()
                    except Exception:
                        pass
                    if getattr(self, "_dica_aberta", None) is estado["jan"]:
                        self._dica_aberta = None
                    estado["jan"] = None

            def mostrar():
                estado["id"] = None
                try:
                    msg = texto() if callable(texto) else texto
                    if not msg or not w.winfo_exists():
                        return
                    jan = tk.Toplevel(self.root)
                    jan.overrideredirect(True)
                    jan.attributes("-topmost", True)
                    tk.Label(jan, text=msg, bg="#2a2e38", fg=COR["texto"], font=self.f_peq,
                             padx=self.px(7), pady=self.px(3), justify="left",
                             highlightthickness=1, highlightbackground="#3a4050").pack()
                    jan.update_idletasks()
                    x = w.winfo_pointerx() + self.px(12)
                    y = w.winfo_pointery() + self.px(16)
                    sw, sh = jan.winfo_screenwidth(), jan.winfo_screenheight()
                    x = min(x, sw - jan.winfo_reqwidth() - 4)
                    if y + jan.winfo_reqheight() > sh:
                        y = w.winfo_pointery() - jan.winfo_reqheight() - self.px(8)
                    jan.geometry(f"+{x}+{y}")
                    jan.lift()
                    estado["jan"] = jan
                    self._dica_aberta = jan
                    estado["inicio"] = time.time()
                    vigiar()
                except Exception:
                    estado["jan"] = None

            def vigiar():
                # some se o mouse saiu do botao (o <Leave> as vezes nao chega)
                # ou depois de 8 segundos
                if estado["jan"] is None:
                    return
                try:
                    px, py = w.winfo_pointerx(), w.winfo_pointery()
                    x0, y0 = w.winfo_rootx(), w.winfo_rooty()
                    dentro = x0 <= px < x0 + w.winfo_width() and y0 <= py < y0 + w.winfo_height()
                except Exception:
                    dentro = False
                if not dentro or time.time() - estado.get("inicio", 0) > 8:
                    esconder()
                    return
                try:
                    estado["jan"].lift()
                except Exception:
                    pass
                self.root.after(250, vigiar)

            def entrar(e=None):
                esconder()
                estado["id"] = self.root.after(500, mostrar)
            w.bind("<Enter>", entrar, add="+")
            w.bind("<Leave>", esconder, add="+")
            w.bind("<ButtonPress>", esconder, add="+")
            w.bind("<Destroy>", esconder, add="+")
            return w

        def botao(self, pai, texto, cmd, cor=None, fonte=None, dica=None):
            b = tk.Label(pai, text=texto, bg=pai["bg"], fg=cor or COR["suave"],
                         font=fonte or self.fontes["botao"], cursor="hand2",
                         padx=self.px(4))
            b.bind("<Button-1>", lambda e: cmd(e))
            b.bind("<Enter>", lambda e: b.configure(fg=COR["texto"]))
            b.bind("<Leave>", lambda e: b.configure(fg=cor or COR["suave"]))
            if dica:
                self.dica(b, dica)
            return b

        # ---------------- cabecalho ----------------
        def _montar_cabecalho(self):
            h = tk.Frame(self.root, bg=COR["fundo"])
            h.pack(fill="x", padx=self.px(8), pady=(self.px(6), self.px(4)))
            self.cab = h
            t = tk.Label(h, text=self._titulo_aba(),
                         fg=COR["alma"], bg=COR["fundo"], font=self.f_titulo)
            t.pack(side="left")
            self.lbl_titulo = t
            self.lbl_personagem = tk.Label(h, text="", fg=COR["texto"],
                                           bg=COR["painel"], font=self.f_peq,
                                           padx=self.px(7), pady=self.px(2))
            self.lbl_personagem.pack(side="left", padx=(self.px(7), 0))
            self.dica(self.lbl_personagem, lambda: self.personagem or "Personagem ainda não identificado")
            self._atualizar_personagem()
            self.b_sincronizar = self.botao(h, "⟳", lambda e: self.pedir_sincronizacao(), cor=COR["ouro"], fonte=self.f_peq,
                                            dica="Sincronizar personagem e progresso da conta")
            self.b_sincronizar.pack(side="left", padx=(self.px(2), 0))
            self.lbl_status = tk.Label(h, text="", bg=COR["fundo"], font=self.f_peq)
            self.lbl_status.pack(side="left", padx=(self.px(8), 0))
            # aparece so quando tem versao nova no GitHub
            self.lbl_atualizar = tk.Label(h, text="", fg=COR["ouro"], bg=COR["fundo"],
                                          font=self.fontes["fechar"], cursor="hand2")
            self.lbl_atualizar.pack(side="left", padx=(self.px(6), 0))
            self.lbl_atualizar.bind("<Button-1>", lambda e: self._clique_atualizar())
            self.dica(self.lbl_atualizar, lambda: "Reabrir com a vers\u00e3o nova (o progresso fica salvo)"
                      if getattr(self, "_versao_no_disco", None) else
                      ("Baixar a vers\u00e3o nova do GitHub" if self.lbl_atualizar.cget("text") else ""))

            self.botao(h, "\u2715", lambda e: self.sair(),
                       dica="Fechar o contador").pack(side="right")
            self.botao(h, "\u2013", lambda e: self.minimizar(),
                       dica="Minimizar (vai para a barra de tarefas)").pack(side="right")
            self.b_od = self.botao(h, "Materiais",
                                   lambda e: self.alternar_aba(
                                       self.cfg.get("aba") if self.cfg.get("aba") in ("od", "materiais") else "od"),
                                   fonte=self.f_nome,
                                   dica="ODs e materiais de coleta")
            self.b_od.pack(side="right")
            self.b_dps = self.botao(h, "DPS", lambda e: self.alternar_aba("dps"),
                                    fonte=self.f_nome,
                                    dica="DPS e dano total dos membros da PT")
            self.b_dps.pack(side="right")
            self.b_pets = self.botao(h, "\u2726 Pets" if self.cfg.get("aba") == "pets" else "Pets",
                                     lambda e: self.alternar_aba("pets"),
                                     dica="Lista de todos os pets e progresso de almas")
            self.b_pets.pack(side="right")
            self.b_inicio = tk.Canvas(h, width=self.px(24), height=self.px(22),
                                      bg=COR["fundo"], highlightthickness=0, cursor="hand2")
            q = self.px
            # Casinha vazada, desenhada com o mesmo traço e cantos arredondados.
            traco = {"fill": COR["suave"], "width": 1.6 * self.k,
                     "capstyle": "round", "joinstyle": "round", "tags": "casa"}
            self.b_inicio.create_line(q(4), q(10), q(12), q(3), q(20), q(10), **traco)
            self.b_inicio.create_line(q(6), q(9), q(6), q(18), q(10), q(18),
                                      q(10), q(13), q(14), q(13), q(14), q(18),
                                      q(18), q(18), q(18), q(9), **traco)
            self.b_inicio.bind("<Enter>", lambda e: self.b_inicio.itemconfigure("casa", fill=COR["ouro"]))
            self.b_inicio.bind("<Leave>", lambda e: self.b_inicio.itemconfigure("casa", fill=COR["suave"]))
            self.b_inicio.bind("<Button-1>", lambda e: self.alternar_aba("almas"))
            self.dica(self.b_inicio, "Voltar ao início: almas recebidas")
            self.b_inicio.pack(side="right")
            self.b_config_top = self.botao(
                h, "\u2699", self.menu_principal,
                dica="Op\u00e7\u00f5es (cor, letra, volume, chat...)")
            self.b_config_top.pack(side="right")

            # A navegação não disputa mais espaço com título, personagem e status.
            # Na largura mínima, todos os destinos continuam clicáveis.
            for antigo in (self.b_od, self.b_dps, self.b_pets, self.b_inicio):
                antigo.destroy()
            self.b_config_top.destroy()
            nav = tk.Frame(self.root, bg=COR["fundo"])
            nav.pack(fill="x", padx=self.px(8), pady=(0, self.px(5)))
            self._nav = nav
            for coluna in range(5):
                nav.grid_columnconfigure(coluna, weight=1, uniform="menu")
            self._nav_inicio = tk.Frame(nav, bg=COR["fundo"], cursor="hand2")
            self._nav_inicio.grid(row=0, column=0, sticky="nsew", padx=self.px(1))
            self.b_inicio = tk.Canvas(self._nav_inicio, width=self.px(25), height=self.px(24),
                                      bg=COR["fundo"], highlightthickness=0, cursor="hand2")
            self.b_inicio.pack()
            q = self.px
            traco = {"fill": COR["suave"], "width": 1.6 * self.k,
                     "capstyle": "round", "joinstyle": "round", "tags": "casa"}
            self.b_inicio.create_line(q(4), q(10), q(12), q(3), q(20), q(10), **traco)
            self.b_inicio.create_line(q(6), q(9), q(6), q(18), q(10), q(18),
                                      q(10), q(13), q(14), q(13), q(14), q(18),
                                      q(18), q(18), q(18), q(9), **traco)
            for widget in (self._nav_inicio, self.b_inicio):
                widget.bind("<Button-1>", lambda e: self.alternar_aba("almas"))
                widget.bind("<Enter>", lambda e: self.b_inicio.itemconfigure("casa", fill=COR["ouro"]))
                widget.bind("<Leave>", lambda e: self._pintar_nav())
            self.dica(self.b_inicio, "Voltar ao início: almas recebidas")
            self.b_pets = self.botao(nav, "Pets", lambda e: self.alternar_aba("pets"),
                                     fonte=self.f_nome, dica="Lista de todos os pets")
            self.b_dps = self.botao(nav, "DPS", lambda e: self.alternar_aba("dps"),
                                    fonte=self.f_nome, dica="DPS dos membros da PT")
            self.b_od = self.botao(nav, "Materiais", lambda e: self.alternar_aba(
                self.cfg.get("aba") if self.cfg.get("aba") in ("od", "materiais") else "od"),
                fonte=self.f_nome, dica="ODs e materiais de coleta")
            b_config = self.botao(nav, "\u2699", self.menu_principal,
                                  fonte=self.f_nome, dica="Opções")
            for coluna, botao in enumerate((self.b_pets, self.b_dps, self.b_od, b_config), 1):
                botao.grid(row=0, column=coluna, sticky="nsew", padx=self.px(1), ipady=self.px(5))
            for botao in (self.b_pets, self.b_dps, self.b_od):
                botao.bind("<Leave>", lambda e: self._pintar_nav())
            self._pintar_nav()
            for w in (h, t, self.lbl_status):
                w.bind("<ButtonPress-1>", self._arrastar_ini)
                w.bind("<B1-Motion>", self._arrastar)
                w.bind("<ButtonRelease-1>", self._arrastar_fim)
            self.atualizar_status()

        def _pintar_nav(self):
            aba = self.cfg.get("aba", "almas")
            for botao, ativo in ((self.b_pets, aba == "pets"),
                                  (self.b_dps, aba == "dps"),
                                  (self.b_od, aba in ("od", "materiais"))):
                botao.configure(bg=COR["painel"] if ativo else COR["fundo"],
                                fg=COR["alma"] if ativo else COR["suave"])
            inicio = aba == "almas"
            fundo = COR["painel"] if inicio else COR["fundo"]
            self._nav_inicio.configure(bg=fundo)
            self.b_inicio.configure(bg=fundo)
            self.b_inicio.itemconfigure("casa", fill=COR["alma"] if inicio else COR["suave"])

        def _arrastar_ini(self, e):
            self._dx = e.x_root - self.root.winfo_x()
            self._dy = e.y_root - self.root.winfo_y()

        def _tamanho_ini(self, e):
            self._tamanho_origem = (e.x_root, e.y_root,
                                    self.root.winfo_width(), self.root.winfo_height())

        def _tamanho_arrastar(self, e):
            x, y, largura, altura = self._tamanho_origem
            largura = max(self.px(320), largura + e.x_root - x)
            altura = max(self.px(180), altura + e.y_root - y)
            self.root.geometry(f"{largura}x{altura}")

        def _tamanho_salvar(self, e):
            self.root.update_idletasks()
            self.cfg["tamanho_janela"] = [self.root.winfo_width(), self.root.winfo_height()]
            self.salvar_cfg()

        def _arrastar(self, e):
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

        def _arrastar_fim(self, e):
            self.cfg["posicao"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.salvar_cfg()

        def atualizar_status(self):
            if self.erro:
                txt, cor = "\u25cf erro na leitura", COR["erro"]
            elif self.cfg.get("fonte_leitura") == "npcap":
                if not self.rede_conectada:
                    txt, cor = "\u25cf aguardando AION 2\u2026", COR["ouro"]
                elif not self.rede_pronta and not self.personagem and not self.rede_sincronizada:
                    txt, cor = "\u25cf conectando Npcap\u2026", COR["ouro"]
                elif self.sincronizacao_pendente:
                    txt, cor = "● reentre no personagem para ler os dados atuais", COR["ouro"]
                elif not self.rede_sincronizada:
                    txt, cor = "\u25cf aguardando dados da conta", COR["ouro"]
                else:
                    txt, cor = "", COR["suave"]
            elif getattr(self, "status_extra", None):
                txt, cor = "\u25cf " + self.status_extra, COR["ouro"]
            elif not self.cfg.get("regiao"):
                txt, cor = "\u25cf procurando o chat\u2026", COR["ouro"]
            else:
                # lendo normalmente: nao mostra nada (le sempre que esta aberto)
                txt, cor = "", COR["suave"]
            if self.lbl_status.cget("text") != txt or self.lbl_status.cget("fg") != cor:
                self.lbl_status.configure(text=txt, fg=cor)

        def _atualizar_personagem(self):
            nome = self.personagem
            texto = "· " + (nome[:16] + "…" if len(nome) > 17 else nome) if nome else ""
            if self.lbl_personagem.cget("text") != texto:
                self.lbl_personagem.configure(text=texto)

        def pedir_sincronizacao(self):
            # A coleção completa só chega em um pacote enviado pelo jogo ao entrar
            # no mundo. Npcap é passivo: reiniciar o decoder não solicita esse estado.
            self.sincronizacao_pendente = True
            self.atualizar_status()
            registrar("NPCAP: sincronização atual solicitada; aguardando estado enviado ao entrar no personagem")

        def leitor_ativo(self):
            return hasattr(self, "leitor") and self.leitor.ativo.is_set()

        def alternar_pausa(self):
            if self.leitor.ativo.is_set():
                self.leitor.ativo.clear()
                if self.leitor_ocr:
                    self.leitor_ocr.ativo.clear()
            else:
                self.rastreador.reiniciar()  # nao conta o que apareceu enquanto pausado
                self.leitor.ativo.set()
                if self.leitor_ocr:
                    self.leitor_ocr.ativo.set()
            self.atualizar_status()

        def alternar_recolher(self):
            self.cfg["recolhido"] = not self.cfg.get("recolhido")
            self.salvar_cfg()
            self.render()

        # ---------------- lista ----------------
        def render(self):
            """Desenha a lista. As linhas sao criadas uma vez so; quando uma alma
            e contada, so os numeros mudam (nada pisca). A lista so e montada de
            novo quando entra/sai monstro."""
            meta = int(self.cfg["meta"])
            todos = self.prog.monstros
            # ordem fixa (ordem em que os monstros apareceram): nao pula de lugar
            # Um pet concluído permanece visível até o usuário fechá-lo no X.
            # Antes, a preferência antiga "mostrar_concluidos" fazia a linha
            # desaparecer exatamente ao completar a terceira estrela.
            lista = list(todos)
            if self.cfg.get("recolhido"):
                # recolhido: so os 3 ultimos que ganharam alma, na ordem da
                # lista (nao ficam trocando de lugar a cada alma)
                ult = sorted(lista, key=lambda m: m.get("ultima_alma", m.get("atualizado", 0)),
                             reverse=True)[:3]
                lista = [m for m in lista if any(m is u for u in ult)]
            # todos ficam na lista; acima de 6 aparece a rolagem
            visiveis, ocultos = lista, 0

            rodape = ""
            mostra_rodape = True  # evento e versao sempre aparecem embaixo
            if mostra_rodape:
                concl_visiveis = sum(m["qtd"] >= meta for m in lista)
                rodape = f"{concl_visiveis}/{len(lista)} conclu\u00eddos"
                if ocultos:
                    rodape += f"  \u00b7  +{ocultos} ocultos"
            vazio = ""
            if not lista:
                vazio = ("Aguardando as almas recebidas pelo AION 2 via Npcap\u2026"
                         if self.cfg.get("fonte_leitura") == "npcap" else
                         "Procurando o chat do jogo\u2026 deixe o jogo aberto com o chat "
                         "vis\u00edvel (linhas \u201cVoc\u00ea obteve\u2026\u201d)."
                         if not self.cfg.get("regiao") else
                         "Chat encontrado. Aguardando a primeira alma\u2026")

            if self.cfg.get("aba") in ("info", "od", "pets", "materiais", "dps"):
                visiveis, vazio = [], ""
            estrutura = (tuple(id(m) for m in visiveis), mostra_rodape, vazio,
                         bool(self.cfg.get("recolhido")), self.cfg.get("aba", "almas"),
                         bool(self.cfg.get("compacto")), self.cfg.get("tamanho_letra"),
                         tuple(self.cfg.get("tiers", [5, 25, 75])), meta, self.cfg.get("max_linhas"))
            if estrutura == getattr(self, "_estrutura", None):
                if self.cfg.get("aba", "almas") == "almas":
                    self._atualizar_abismo()
                if self.cfg.get("aba") == "info":
                    self._atualizar_info()
                elif self.cfg.get("aba") == "od":
                    self._atualizar_od()
                elif self.cfg.get("aba") == "materiais":
                    self._atualizar_materiais()
                elif self.cfg.get("aba") == "pets":
                    self._atualizar_pets()
                elif self.cfg.get("aba") == "dps":
                    self._atualizar_dps()
                # so atualiza os numeros (sem apagar nada)
                for m in visiveis:
                    w = self._ui.get(id(m))
                    if w and (w.get("_ultima_qtd") != m["qtd"] or
                              w.get("_ultimo_nome") != m["nome"] or
                              w.get("_realce_ativo")):
                        self._atualizar_linha(m, meta, w)
                if getattr(self, "_lbl_rodape", None) and self._lbl_rodape.cget("text") != rodape:
                    self._lbl_rodape.configure(text=rodape)
                return

            if getattr(self, "editando", None) is not None:
                # usuario digitando na caixinha: monta de novo depois
                self.root.after(500, self.render_em_breve)
                return
            self._estrutura = estrutura
            # A reconstrução invalida os widgets da aba anterior antes de
            # qualquer atualizador tentar acessá-los novamente.
            self._materiais_ui = None
            self._od_ui = None
            self._dps_ui = None
            for w in self.corpo.winfo_children():
                w.destroy()
            self._ui = {}
            self._lbl_rodape = None
            if vazio:
                tk.Label(self.corpo, text=vazio, fg=COR["suave"], bg=COR["fundo"],
                         font=self.f_peq, wraplength=self.px(240),
                         justify="left").pack(anchor="w", pady=self.px(4))
            self._rolagem = None
            self._info = None
            limite = max(1, int(self.cfg.get("max_linhas", 6)))
            if self.cfg.get("aba") in ("info", "od", "pets", "materiais", "dps"):
                # mesma largura da lista de almas (o contador nao muda de tamanho)
                chave = f"{self.cfg.get('compacto')}-{self.cfg.get('tamanho_letra')}"
                larg = (self.cfg.get("larguras") or {}).get(chave) or getattr(self, "_larg_almas", 0)
                tk.Frame(self.corpo, bg=COR["fundo"], height=0, width=larg).pack()
                if self.cfg.get("aba") in ("od", "materiais"):
                    abas_coleta = tk.Frame(self.corpo, bg=COR["fundo"])
                    abas_coleta.pack(fill="x", pady=(self.px(3), self.px(7)))
                    for destino, rotulo in (("od", "ODs"), ("materiais", "Materiais")):
                        ativo = self.cfg.get("aba") == destino
                        b = self.botao(abas_coleta, rotulo,
                                       lambda e, d=destino: self.selecionar_coleta(d),
                                       cor=COR["alma"] if ativo else COR["suave"],
                                       fonte=self.f_nome)
                        b.configure(bg=COR["painel"] if ativo else COR["fundo"],
                                    pady=self.px(6), padx=self.px(12))
                        b.pack(side="left", fill="x", expand=True, padx=self.px(2))
                if self.cfg.get("aba") == "od":
                    self._montar_od()
                elif self.cfg.get("aba") == "materiais":
                    self._montar_materiais()
                elif self.cfg.get("aba") == "pets":
                    self._montar_pets()
                elif self.cfg.get("aba") == "dps":
                    self._montar_dps()
                else:
                    self._montar_info()
            elif len(visiveis) > limite:
                pai = self._montar_rolagem()
                for m in visiveis:
                    self._ui[id(m)] = self._linha(m, meta, pai)
                self._ajustar_rolagem(visiveis[:limite])
            else:
                for m in visiveis:
                    self._ui[id(m)] = self._linha(m, meta)
            self._lbl_evento = None
            if mostra_rodape:
                if self.cfg.get("aba") == "almas":
                    caixa_abismo = tk.Frame(self.corpo, bg=COR["painel"],
                                            highlightthickness=1, highlightbackground=COR["barra"])
                    caixa_abismo.pack(fill="x", pady=(self.px(7), 0))
                    caixa_abismo.columnconfigure(1, weight=1)
                    tk.Label(caixa_abismo, text="◈  Abismo", fg=COR["ouro"],
                             bg=COR["painel"], font=self.f_nome).grid(
                                 row=0, column=0, sticky="w", padx=self.px(8), pady=self.px(5))
                    pontos_fmt = f"{self.pontos_abismo:,}".replace(",", ".")
                    kinah_fmt = f"{self.kinah_abismo:,}".replace(",", ".")
                    self._lbl_abismo = tk.Label(caixa_abismo,
                                                text=f"Pontos: +{pontos_fmt}   ·   Kinah: +{kinah_fmt}",
                                                fg=COR["texto"], bg=COR["painel"], font=self.f_peq)
                    self._lbl_abismo.grid(row=0, column=1, sticky="e", padx=self.px(8))
                    tempo = tk.Frame(caixa_abismo, bg=COR["painel"])
                    tempo.grid(row=1, column=0, columnspan=2, sticky="ew",
                               padx=self.px(8), pady=(0, self.px(5)))
                    tk.Label(tempo, text="Tempo de farm", fg=COR["suave"],
                             bg=COR["painel"], font=self.f_peq).pack(side="left")
                    self._lbl_crono_home = tk.Label(
                        tempo, text="", fg=COR["texto"], bg=COR["painel"],
                        font=self.fontes["relogio"])
                    self._lbl_crono_home.pack(side="left", padx=(self.px(6), 0))
                    self._b_crono_home = self.botao(
                        tempo, "▶", lambda e: self.crono_alternar(), fonte=self.f_peq,
                        dica=lambda: "Pausar o cronômetro" if self._crono().get("inicio")
                        else "Começar o cronômetro")
                    self._b_crono_home.pack(side="right")
                    self.botao(tempo, "↺", lambda e: self.crono_zerar(),
                               fonte=self.f_peq, dica="Zerar o cronômetro").pack(side="right")
                rod = tk.Frame(self.corpo, bg=COR["fundo"])
                rod.pack(fill="x", pady=(self.px(4), 0))
                self._lbl_rodape = tk.Label(rod, text=rodape, fg=COR["suave"],
                                            bg=COR["fundo"], font=self.f_peq)
                self._lbl_rodape.pack(side="left")
                tk.Label(rod, text=f"v{VERSAO}", fg="#5a6070", bg=COR["fundo"],
                         font=self.fontes["versao"]).pack(side="left", padx=(self.px(6), 0))
                self._lbl_relogio = tk.Label(rod, text="", fg=COR["ouro"], bg=COR["fundo"],
                                             font=self.fontes["relogio"], width=7, anchor="e")
                self._lbl_relogio.pack(side="right")
                self._lbl_evento = tk.Label(rod, text="", fg=COR["ouro"],
                                            bg=COR["fundo"], font=self.fontes["evento"])
                self._lbl_evento.pack(side="right")
                self._ev_cache = None
                self.atualizar_evento(agendar=False)

        # ---------------- rolagem da lista ----------------
        def _montar_rolagem(self):
            caixa = tk.Frame(self.corpo, bg=COR["fundo"])
            caixa.pack(fill="x")
            cv = tk.Canvas(caixa, bg=COR["fundo"], highlightthickness=0, bd=0)
            cv.pack(side="left", fill="both", expand=True)
            barra = tk.Canvas(caixa, width=self.px(5), height=1, bg=COR["fundo"], highlightthickness=0, bd=0)
            barra.pack(side="right", fill="y", padx=(self.px(3), 0))
            dentro = tk.Frame(cv, bg=COR["fundo"])
            cv.create_window((0, 0), window=dentro, anchor="nw", tags="dentro")
            pino = barra.create_rectangle(0, 0, 0, 0, fill="#3a3f4c", outline="")

            def pintar(a, b):
                a, b = float(a), float(b)
                H = barra.winfo_height()
                barra.coords(pino, 0, int(a * H), barra.winfo_width(), max(int(a * H) + 8, int(b * H)))
            cv.configure(yscrollcommand=pintar)
            cv.bind("<Configure>", lambda e: cv.itemconfigure("dentro", width=e.width))

            def roda(e):
                passos = -1 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else 1
                cv.yview_scroll(passos, "units")
                return "break"

            def entrar(e):
                self.root.bind_all("<MouseWheel>", roda)
                self.root.bind_all("<Button-4>", roda)
                self.root.bind_all("<Button-5>", roda)

            def sair(e):
                self.root.unbind_all("<MouseWheel>")
                self.root.unbind_all("<Button-4>")
                self.root.unbind_all("<Button-5>")
            caixa.bind("<Enter>", entrar)
            caixa.bind("<Leave>", sair)

            def arrastar(e):
                H = max(1, barra.winfo_height())
                a, b = cv.yview()
                cv.yview_moveto(max(0.0, min(1.0, e.y / H - (b - a) / 2)))
            barra.bind("<Button-1>", arrastar)
            barra.bind("<B1-Motion>", arrastar)
            cv.configure(yscrollincrement=self.px(12))
            self._rolagem = (cv, dentro)
            return dentro

        def _ajustar_rolagem(self, primeiros):
            cv, dentro = self._rolagem
            dentro.update_idletasks()
            folga = self.px(2) if self.cfg.get("compacto") else self.px(4)
            altura = sum(self._ui[id(m)]["frame"].winfo_reqheight() + folga for m in primeiros) - folga
            cv.configure(height=altura, width=dentro.winfo_reqwidth(),
                         scrollregion=(0, 0, dentro.winfo_reqwidth(), dentro.winfo_reqheight()))
            dentro.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))

        def mostrar_linha(self, m):
            """Rola a lista ate o monstro (ex.: ganhou alma e estava escondido)."""
            rol = getattr(self, "_rolagem", None)
            w = self._ui.get(id(m)) if hasattr(self, "_ui") else None
            if not rol or not w:
                return
            cv, dentro = rol
            f = w["frame"]
            total = max(1, dentro.winfo_height())
            topo, alt = f.winfo_y(), f.winfo_height()
            vis_a = cv.canvasy(0)
            vis_b = vis_a + cv.winfo_height()
            if topo < vis_a:
                cv.yview_moveto(topo / total)
            elif topo + alt > vis_b:
                cv.yview_moveto(max(0, topo + alt - cv.winfo_height()) / total)

        # ---------------- aba Info ----------------
        def _titulo_aba(self):
            return {"info": "\u25f7 Info", "od": "Materiais", "materiais": "Materiais",
                    "pets": "\u2661 Pets", "dps": "DPS"}.get(
                        self.cfg.get("aba"), "\u2726 Almas")

        def selecionar_coleta(self, destino):
            if self.cfg.get("aba") != destino:
                self.alternar_aba(destino)

        def alternar_aba(self, destino="info"):
            """Botao da aba: abre a aba; clicando de novo volta para as almas."""
            if getattr(self, "editando", None) is not None:
                return
            self._medir_largura_almas()
            atual = self.cfg.get("aba", "almas")
            proxima = "almas" if atual == destino else destino
            altura_restaurar = None
            if proxima == "materiais" and atual != "materiais":
                self._altura_antes_materiais = self.root.winfo_height()
                self._altura_materiais_chave = None
            elif atual == "materiais" and proxima != "materiais":
                altura_restaurar = getattr(self, "_altura_antes_materiais", None)
            campos = ("_estrutura", "_ui", "_lbl_rodape", "_rolagem", "_info",
                      "_od_ui", "_materiais_ui", "_lbl_abismo", "_lbl_crono", "_b_crono", "_lbl_crono_home",
                      "_b_crono_home", "_pets_tree", "_dps_ui",
                      "_pets_resumo", "_pets_busca", "_lbl_evento",
                      "_lbl_relogio", "_ev_cache", "_ev_quando")
            self._abas_visuais[atual] = {
                "corpo": self.corpo,
                **{campo: getattr(self, campo, None) for campo in campos},
            }
            for evento in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.root.unbind_all(evento)
            self.corpo.pack_forget()
            salva = self._abas_visuais.get(proxima)
            if salva:
                self.corpo = salva["corpo"]
                for campo in campos:
                    setattr(self, campo, salva[campo])
            else:
                self.corpo = tk.Frame(self.area_abas, bg=COR["fundo"])
                for campo in campos:
                    setattr(self, campo, {} if campo == "_ui" else None)
            self.corpo.pack(fill="both", expand=True)
            self.cfg["aba"] = proxima
            self.salvar_cfg()
            aba = self.cfg["aba"]
            self.lbl_titulo.configure(text=self._titulo_aba())
            self.b_od.configure(text="Materiais")
            self.b_dps.configure(text="\u2726 DPS" if aba == "dps" else "DPS")
            self.b_pets.configure(text="\u2726 Pets" if aba == "pets" else "Pets")
            self._pintar_nav()
            self.render()
            if altura_restaurar:
                self.root.geometry(f"{self.root.winfo_width()}x{int(altura_restaurar)}")

        def _medir_largura_almas(self):
            try:
                self.corpo.update_idletasks()
                if self.cfg.get("aba") == "almas":
                    self._larg_almas = self.corpo.winfo_reqwidth()
                    chave = f"{self.cfg.get('compacto')}-{self.cfg.get('tamanho_letra')}"
                    larguras = self.cfg.setdefault("larguras", {})
                    if larguras.get(chave) != self._larg_almas:
                        larguras[chave] = self._larg_almas
                        self.salvar_cfg()
            except Exception:
                pass

        # ---------------- DPS da PT ----------------
        @staticmethod
        def _numero_dps(valor):
            valor = float(valor)
            for divisor, sufixo in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
                if valor >= divisor:
                    return f"{valor / divisor:.1f}{sufixo}"
            return f"{valor:.0f}"

        def _montar_dps(self):
            boss = tk.Frame(self.corpo, bg=COR["painel"], highlightthickness=1,
                            highlightbackground=COR["barra"])
            boss.pack(fill="x", pady=(self.px(1), self.px(3)))
            boss_linha = tk.Frame(boss, bg=COR["painel"])
            boss_linha.pack(fill="x", padx=self.px(6), pady=(self.px(2), self.px(1)))
            boss_nome = tk.Label(boss_linha, text="Boss", fg=COR["ouro"], bg=COR["painel"],
                                 font=self.f_nome, anchor="w")
            boss_nome.pack(side="left")
            boss_valor = tk.Label(boss_linha, text="Aguardando combate", fg=COR["suave"],
                                  bg=COR["painel"], font=self.f_peq, anchor="e")
            boss_valor.pack(side="right")
            boss_barra = tk.Canvas(boss, height=self.px(5), bg=COR["barra"],
                                   highlightthickness=0, bd=0)
            boss_barra.pack(fill="x", padx=self.px(6), pady=(0, self.px(3)))
            boss_fill = boss_barra.create_rectangle(0, 0, 0, self.px(5),
                                                     fill=COR["ouro"], width=0)
            topo = tk.Frame(self.corpo, bg=COR["fundo"])
            topo.pack(fill="x", pady=(0, self.px(4)))
            resumo = tk.Label(topo, text="", fg=COR["ouro"], bg=COR["fundo"],
                              font=self.f_nome, anchor="w")
            resumo.pack(side="left")
            self.botao(topo, "\u21ba", lambda e: self.dps_zerar(), fonte=self.f_peq,
                       dica="Zerar dano e iniciar uma nova medição").pack(side="right")
            status = tk.Label(self.corpo, text="", fg=COR["suave"], bg=COR["fundo"],
                              font=self.f_peq, anchor="w", justify="left")
            area = tk.Frame(self.corpo, bg=COR["fundo"])
            area.pack(fill="x")
            rows = {}
            for name in self.dps_party:
                card = tk.Frame(area, bg=COR["painel"], highlightthickness=1,
                                highlightbackground=COR["barra"])
                card.pack(fill="x", pady=(0, self.px(2)))
                line = tk.Frame(card, bg=COR["painel"])
                line.pack(fill="x", padx=self.px(5), pady=(self.px(2), self.px(1)))
                line.columnconfigure(0, weight=1)
                name_label = tk.Label(line, text=name, fg=COR["texto"], bg=COR["painel"],
                                      font=self.f_nome, anchor="w")
                name_label.grid(row=0, column=0, sticky="ew")
                sub = tk.Label(line, text="", fg=COR["suave"], bg=COR["painel"],
                               font=self.f_peq, anchor="e")
                sub.grid(row=0, column=1, padx=(self.px(4), self.px(6)))
                value = tk.Label(line, text="", fg=COR["alma"], bg=COR["painel"],
                                 font=self.fontes["relogio"], anchor="e")
                value.grid(row=0, column=2, sticky="e")
                bar = tk.Canvas(card, height=self.px(2), bg=COR["barra"],
                                highlightthickness=0, bd=0)
                bar.pack(fill="x", padx=self.px(5), pady=(0, self.px(2)))
                fill = bar.create_rectangle(0, 0, 0, self.px(2), fill=COR["alma"], width=0)
                rows[name] = (value, sub, bar, fill)
            self._dps_ui = (resumo, status, rows, boss_valor, boss_barra, boss_fill)
            self._atualizar_dps()

            def pulse():
                if self.cfg.get("aba") == "dps" and getattr(self, "_dps_ui", None):
                    self._atualizar_dps()
                    self.root.after(500, pulse)
            self.root.after(500, pulse)

        def _atualizar_dps(self):
            ui = getattr(self, "_dps_ui", None)
            if not ui or self.cfg.get("aba") != "dps":
                return
            resumo, status, rows, boss_valor, boss_barra, boss_fill = ui
            atual, maxima = self.boss_health
            percentual = max(0.0, min(100.0, 100.0 * atual / maxima)) if maxima else 0.0
            boss_valor.configure(text=(f"{percentual:.1f}%  ·  {self._numero_dps(atual)} / "
                                        f"{self._numero_dps(maxima)}" if maxima else
                                        "Aguardando boss"))
            largura_boss = max(1, boss_barra.winfo_width())
            boss_barra.coords(boss_fill, 0, 0, int(largura_boss * percentual / 100), self.px(5))
            if not self.dps_party:
                resumo.configure(text="PT atual")
                status.configure(text="Aguardando a lista da PT enviada pelo jogo…")
                return
            now = time.monotonic()
            end = (now if self.dps_last_hit and now - self.dps_last_hit < 10
                   else self.dps_last_hit)
            seconds = max(1.0, (end - self.dps_started) if end and self.dps_started else 1.0)
            total = sum(self.dps_damage.get(name, 0) for name in self.dps_party)
            grupo = "Solo" if len(self.dps_party) == 1 else f"{len(self.dps_party)} na PT"
            resumo.configure(text=(f"{grupo} · {int(seconds) // 60:02d}:{int(seconds) % 60:02d}"
                                    f" · {self._numero_dps(total)}"))
            status.configure(text=(f"Dano total da PT: {self._numero_dps(total)}" if total else
                                   "PT encontrada. Aguardando o primeiro dano…"))
            biggest = max((self.dps_damage.get(name, 0) for name in self.dps_party), default=1) or 1
            for name, (value, sub, bar, fill) in rows.items():
                damage = self.dps_damage.get(name, 0)
                dps = damage / seconds
                share = 100 * damage / total if total else 0
                value.configure(text=f"{self._numero_dps(dps)} DPS")
                sub.configure(text=f"{self._numero_dps(damage)} · {share:.0f}%")
                width = max(1, bar.winfo_width())
                bar.coords(fill, 0, 0, int(width * damage / biggest), self.px(2))

        def dps_zerar(self):
            self.dps_damage = {name: 0 for name in self.dps_party}
            self.dps_started = None
            self.dps_last_hit = None
            self._atualizar_dps()

        # ---------------- catálogo completo de pets ----------------
        def _montar_pets(self):
            from tkinter import ttk
            topo = tk.Frame(self.corpo, bg=COR["painel"], padx=self.px(8), pady=self.px(6))
            topo.pack(fill="x", pady=(0, self.px(5)))
            tk.Label(topo, text="Pets", fg=COR["ouro"], bg=COR["painel"],
                     font=self.f_nome).pack(side="left", padx=(0, self.px(8)))
            tk.Label(topo, text="Buscar", fg=COR["suave"], bg=COR["painel"],
                     font=self.f_peq).pack(side="left", padx=(0, self.px(5)))
            self._pets_busca = tk.StringVar()
            busca = tk.Entry(topo, textvariable=self._pets_busca, font=self.f_nome,
                             fg=COR["texto"], bg=COR["barra"],
                             insertbackground=COR["texto"], relief="flat")
            busca.pack(side="left", fill="x", expand=True)
            busca.bind("<KeyRelease>", lambda e: self._atualizar_pets())
            self._pets_so_incompletos = tk.BooleanVar(value=False)
            tk.Checkbutton(topo, text="Só incompletos", variable=self._pets_so_incompletos,
                           command=self._atualizar_pets, fg=COR["texto"], bg=COR["painel"],
                           activebackground=COR["painel"], activeforeground=COR["ouro"],
                           selectcolor=COR["painel"], font=self.f_peq).pack(side="right")

            estilo = ttk.Style(self.root)
            # O tema nativo do Windows ignora as cores do cabeçalho do Treeview.
            if estilo.theme_use() != "clam":
                estilo.theme_use("clam")
            estilo.configure("Pets.Treeview", background=COR["painel"],
                             fieldbackground=COR["painel"], foreground=COR["texto"],
                             rowheight=self.px(32), font=self.f_peq, borderwidth=0)
            estilo.configure("Pets.Treeview.Heading", background=COR["barra"],
                             foreground=COR["ouro"], font=self.f_peq,
                             relief="flat", padding=(self.px(3), self.px(5)))
            estilo.map("Pets.Treeview.Heading",
                       background=[("active", COR["barra"])],
                       foreground=[("active", COR["texto"])])
            estilo.map("Pets.Treeview", background=[("selected", COR["destaque"])],
                        foreground=[("selected", COR["texto"])])
            estilo.configure("Pets.Vertical.TScrollbar", background=COR["barra"],
                             troughcolor=COR["fundo"], arrowcolor=COR["texto"],
                             bordercolor=COR["fundo"], lightcolor=COR["barra"],
                             darkcolor=COR["barra"])
            caixa = tk.Frame(self.corpo, bg=COR["fundo"])
            caixa.pack(fill="both", expand=True)
            tree = ttk.Treeview(caixa, columns=("pet", "almas", "faltam"),
                                show="tree headings", height=11, style="Pets.Treeview",
                                selectmode="browse")
            tree.heading("#0", text="Estrelas")
            tree.column("#0", width=self.px(76), minwidth=self.px(76),
                        stretch=False, anchor="center")
            for chave, titulo, largura in (("pet", "Pet", 170),
                                            ("almas", "Almas", 78),
                                            ("faltam", "Faltam", 51)):
                tree.heading(chave, text=titulo)
                tree.column(chave, width=self.px(largura),
                            minwidth=self.px(42), stretch=chave == "pet",
                            anchor="w" if chave == "pet" else "center")
            barra = ttk.Scrollbar(caixa, orient="vertical", command=tree.yview,
                                  style="Pets.Vertical.TScrollbar")
            tree.configure(yscrollcommand=barra.set)
            tree.pack(side="left", fill="both", expand=True)
            barra.pack(side="right", fill="y")
            self._pets_tree = tree
            self._pets_estrelas_cache = {}
            self._pets_resumo = tk.Label(self.corpo, fg=COR["suave"],
                                         bg=COR["fundo"], font=self.f_peq,
                                         justify="left", anchor="w")
            self._pets_resumo.pack(fill="x", pady=(self.px(3), 0))
            self._atualizar_pets()

        def _montar_radar(self):
            radar = tk.Frame(self.corpo, bg=COR["painel"],
                             highlightthickness=1, highlightbackground=COR["barra"])
            radar.pack(fill="x", pady=(self.px(7), 0))
            topo = tk.Frame(radar, bg=COR["painel"])
            topo.pack(fill="x", padx=self.px(7), pady=(self.px(5), 0))
            tk.Label(topo, text="◉ Radar de mascotes", fg=COR["ouro"],
                     bg=COR["painel"], font=self.f_nome).pack(side="left")
            mapas = {}
            for chave in ("verteron", "altgard", "abyss"):
                botao = tk.Label(topo, text=RADAR_MAPAS[chave][0],
                                  bg=COR["painel"], fg=COR["suave"],
                                  font=self.f_peq, cursor="hand2")
                botao.pack(side="right", padx=(self.px(4), 0))
                botao.bind("<Button-1>", lambda e, k=chave: self._selecionar_mapa_radar(k))
                mapas[chave] = botao
            status = tk.Label(radar, text="", fg=COR["suave"], bg=COR["painel"],
                              font=self.f_peq, anchor="w", justify="left",
                              wraplength=self.px(355))
            status.pack(fill="x", padx=self.px(7), pady=(self.px(2), self.px(3)))
            conteudo = tk.Frame(radar, bg=COR["painel"])
            conteudo.pack(fill="x", padx=self.px(7))
            tamanho = self.px(180)
            mapa = tk.Canvas(conteudo, width=tamanho, height=tamanho,
                             bg=COR["fundo"], highlightthickness=0)
            mapa.pack(side="left", anchor="n")
            lista = tk.Frame(conteudo, bg=COR["painel"])
            lista.pack(side="left", fill="both", expand=True,
                       padx=(self.px(7), 0), anchor="n")
            rows = []
            for _ in range(3):
                row = tk.Frame(lista, bg=COR["painel"])
                linha = tk.Label(row, text="", fg=COR["texto"], bg=COR["painel"],
                                 font=self.f_peq, anchor="w", justify="left",
                                 wraplength=self.px(175))
                linha.pack(fill="x")
                detalhe = tk.Label(row, text="", fg=COR["suave"], bg=COR["painel"],
                                   font=self.f_peq, anchor="w", justify="left",
                                   wraplength=self.px(175))
                detalhe.pack(fill="x")
                rows.append((row, linha, detalhe))
            legenda = tk.Label(radar, text="● bicho com alma   ·   • local de farm   ·   ○ área estimada",
                               fg=COR["suave"], bg=COR["painel"],
                               font=self.f_peq, anchor="w")
            legenda.pack(fill="x", padx=self.px(7), pady=(self.px(3), self.px(5)))
            self._radar_ui = (radar, status, rows, mapa, mapas)
            self._radar_assinatura = None
            self._atualizar_radar()

        def _selecionar_mapa_radar(self, chave):
            self.radar_mapa_escolhido = chave
            self.cfg["radar_mapa"] = chave
            self.salvar_cfg()
            self._radar_assinatura = None
            self._radar_mapa_visual = None
            self._atualizar_radar()

        def _desenhar_mapa_radar(self, catalogo, especies, conhecidos):
            from PIL import Image, ImageTk
            ui = self._radar_ui
            canvas = ui[3]
            chave = self.radar_mapa_escolhido
            meta = RADAR_MAPAS[chave]
            pos = self._radar_pos_visual
            pendentes = {str(r[0]) for r in catalogo
                         if str(r[3]).isdigit() and int(r[3]) > 0}
            mobs = tuple(sorted((entity, npc, x, y) for entity, (npc, x, y, _) in
                                self.radar_mobs.items() if str(npc) in especies and
                                str(especies[str(npc)][0]) in pendentes))
            assinatura = (chave, tuple(pos[:2]) if pos else None,
                          getattr(self, "_radar_pos_estimada_ativa", False),
                          mobs, tuple((r[5], r[6]) for r in conhecidos),
                          tuple(sorted(pendentes)) if not pos else ())
            if assinatura == self._radar_assinatura:
                return
            self._radar_assinatura = assinatura
            tamanho = self.px(180)
            if chave not in self._radar_imagens:
                caminho = os.path.join(BASE, f"radar_mapa_{chave}.jpg")
                try:
                    self._radar_imagens[chave] = Image.open(caminho).convert("RGB")
                except (OSError, ValueError):
                    self._radar_imagens[chave] = None
            imagem = self._radar_imagens[chave]
            if imagem is None:
                canvas.delete("all")
                canvas.create_text(tamanho // 2, tamanho // 2,
                                   text="Mapa indisponível", fill=COR["suave"])
                return
            escala, offset_x, offset_y, fator = meta[2:]
            estimada = getattr(self, "_radar_pos_estimada_ativa", False)
            raio_m = RADAR_RAIO_ESTIMADO if estimada else RADAR_RAIO_EXATO
            if pos:
                px = (offset_x + escala * pos[0]) * fator
                py = (offset_y + escala * pos[1]) * fator
                meio = escala * fator * raio_m * 100
                recorte = imagem.crop((round(px - meio), round(py - meio),
                                       round(px + meio), round(py + meio)))
                fundo = recorte.resize((tamanho, tamanho), Image.Resampling.BILINEAR)
            else:
                fundo = imagem.resize((tamanho, tamanho), Image.Resampling.BILINEAR)
            self._radar_foto = ImageTk.PhotoImage(fundo, master=self.root)
            fundo_item = canvas.find_withtag("radar_fundo")
            if fundo_item:
                canvas.itemconfigure(fundo_item[0], image=self._radar_foto)
            else:
                canvas.create_image(0, 0, image=self._radar_foto, anchor="nw",
                                    tags="radar_fundo")
            canvas.delete("radar_marcas")
            if pos:
                raio = raio_m * 100
                def ponto(x, y):
                    return (tamanho / 2 + (x - pos[0]) * tamanho / (2 * raio),
                            tamanho / 2 + (y - pos[1]) * tamanho / (2 * raio))
                for r in conhecidos[:100]:
                    x, y = ponto(r[5], r[6])
                    canvas.create_oval(x - 3, y - 3, x + 3, y + 3,
                                       fill="#af8240", outline="#453018",
                                       tags="radar_marcas")
                for _, npc, xw, yw in mobs:
                    if str(npc) not in especies:
                        continue
                    x, y = ponto(xw, yw)
                    if 0 <= x <= tamanho and 0 <= y <= tamanho:
                        canvas.create_oval(x - 4, y - 4, x + 4, y + 4,
                                           fill=COR["ouro"], outline="#251b0b",
                                           width=2, tags="radar_marcas")
                alvo = getattr(self, "_radar_pos_exibicao", pos)
                x, y = ponto(alvo[0], alvo[1])
                canvas.create_oval(x - 5, y - 5, x + 5, y + 5,
                                   fill="" if estimada else "#70bdff",
                                   outline="#70bdff" if estimada else "white",
                                   width=2, tags="radar_marcas")
            else:
                pontos = self.radar_pontos.get(chave, {})
                amostra = []
                for npc, coordenadas in pontos.items():
                    especie = especies.get(str(npc))
                    if especie and str(especie[0]) in pendentes:
                        amostra.extend(coordenadas)
                passo = max(1, len(amostra) // 250)
                for xw, yw in amostra[::passo][:250]:
                    x = (offset_x + escala * xw) * fator * tamanho / imagem.width
                    y = (offset_y + escala * yw) * fator * tamanho / imagem.height
                    if 0 <= x <= tamanho and 0 <= y <= tamanho:
                        canvas.create_oval(x - 1, y - 1, x + 1, y + 1,
                                           fill="#d6ab56", outline="",
                                           tags="radar_marcas")
            if not canvas.find_withtag("radar_borda"):
                canvas.create_rectangle(0, 0, tamanho - 1, tamanho - 1,
                                        outline=COR["barra"], tags="radar_borda")

        def _imagem_estrelas_pet(self, andamento):
            from PIL import Image, ImageTk
            nivel = 0
            frac = 0.0
            achado = re.fullmatch(r"Nv\.(\d+)\s+(\d+)/(\d+)", andamento)
            if achado:
                nivel = min(int(achado.group(1)), 3)
                atual, meta = int(achado.group(2)), int(achado.group(3))
                anterior = (self.cfg.get("tiers", [5, 25, 75])[nivel - 1]
                            if nivel else 0)
                frac = max(0.0, min(1.0, (atual - anterior) /
                                     max(1, meta - anterior)))
            elif andamento == "Nv.3 MAX":
                nivel = 3
            # Arredondar evita criar centenas de imagens durante a captura.
            decimos = round(frac * 10)
            chave = (nivel, decimos)
            cache = self._pets_estrelas_cache
            if chave not in cache:
                tamanho = self.px(16)
                espaco = self.px(2)
                img = Image.new("RGBA", (3 * tamanho + 2 * espaco, tamanho),
                                COR["painel"])
                for i in range(3):
                    estado = "cheia" if i < nivel else "atual" if i == nivel and decimos else "vazia"
                    estrela = imagem_estrela(estado, decimos / 10, tamanho,
                                             COR["painel"])
                    img.paste(estrela, (i * (tamanho + espaco), 0))
                cache[chave] = ImageTk.PhotoImage(img, master=self.root)
            return cache[chave]

        def _atualizar_pets(self):
            tree = getattr(self, "_pets_tree", None)
            if self.cfg.get("aba") != "pets" or tree is None or not tree.winfo_exists():
                return
            linhas = self.prog.catalogo_pets(self.nomes_pets,
                                            self.cfg.get("tiers", [5, 25, 75]))
            busca = normalizar(self._pets_busca.get()).replace(" ", "")
            visiveis = [r for r in linhas if busca in normalizar(r[1]).replace(" ", "")
                        or busca in r[0]]
            if self._pets_so_incompletos.get():
                visiveis = [r for r in visiveis if r[3] != "0"]
            ids = tuple(r[0] for r in visiveis)
            if tuple(tree.get_children()) != ids:
                tree.delete(*tree.get_children())
                for pet_id, nome, almas, faltam, origem in visiveis:
                    tree.insert("", "end", iid=pet_id,
                                image=self._imagem_estrelas_pet(almas),
                                values=(nome, almas, "✓" if faltam == "0" else faltam))
            else:
                for pet_id, nome, almas, faltam, origem in visiveis:
                    valores = (nome, almas, "✓" if faltam == "0" else faltam)
                    if tuple(tree.item(pet_id, "values")) != valores:
                        tree.item(pet_id, values=valores)
                    imagem = self._imagem_estrelas_pet(almas)
                    if tree.item(pet_id, "image") != str(imagem):
                        tree.item(pet_id, image=imagem)
            estado = ("Sincronizado com o jogo" if self.rede_sincronizada else
                      "Valores salvos; entre no personagem para confirmar")
            resumo = f"{len(visiveis)}/{len(linhas)} pets · {estado}"
            if self._pets_resumo.cget("text") != resumo:
                self._pets_resumo.configure(text=resumo)

        def _radar_pulso(self):
            if self.cfg.get("aba", "almas") == "almas":
                self._atualizar_radar()
            self.root.after(500, self._radar_pulso)

        def _radar_animar(self):
            agora = time.monotonic()
            intervalo = agora - self._radar_ultimo_frame
            self._radar_ultimo_frame = agora
            if (self.cfg.get("aba", "almas") == "almas" and
                    self._radar_cache is not None and self._radar_ui and
                    self._radar_ui[0].winfo_exists()):
                alvo = getattr(self, "_radar_pos_exibicao", None)
                if alvo is not None and self._radar_pos_visual is not None:
                    proxima = avancar_centro_radar(self._radar_pos_visual,
                                                   alvo, intervalo)
                    dx = proxima[0] - self._radar_pos_visual[0]
                    dy = proxima[1] - self._radar_pos_visual[1]
                    if dx * dx + dy * dy >= 40 * 40 or proxima == alvo[:2]:
                        self._radar_pos_visual = proxima
                        self._desenhar_mapa_radar(*self._radar_cache)
            self.root.after(50, self._radar_animar)

        def _ao_radar_rede(self, evento):
            tipo = evento[0]
            agora = time.monotonic()
            if tipo == "zone":
                _, zona, x, y = evento
                if (zona != self.radar_zona or
                        (self.radar_posicao is not None and
                         ((x - self.radar_posicao[0]) ** 2 +
                          (y - self.radar_posicao[1]) ** 2) > 15_000 ** 2)):
                    self.radar_mobs.clear()
                    self.radar_posicao_estimada = None
                self.radar_zona = zona
                self.radar_posicao = (x, y, agora)
                for chave, meta in RADAR_MAPAS.items():
                    if zona in meta[1]:
                        self.radar_mapa_escolhido = chave
                        self.cfg["radar_mapa"] = chave
                        break
            elif tipo == "position":
                self.radar_posicao = (evento[1], evento[2], agora)
            elif tipo == "mob_spawn":
                _, entity, npc, x, y = evento
                if str(npc) in getattr(self.leitor, "radar_mascotes", {}):
                    self.radar_mobs[entity] = (npc, x, y, agora)
                    mapas = []
                    for chave, pontos in self.radar_pontos.items():
                        coordenadas = pontos.get(str(npc), ())
                        if coordenadas:
                            menor = min((x - px) ** 2 + (y - py) ** 2
                                        for px, py in coordenadas)
                            mapas.append((menor, chave))
                    if mapas:
                        _, chave = min(mapas)
                        if self.radar_mapa_estimado and chave != self.radar_mapa_estimado:
                            self.radar_mobs.clear()
                            self.radar_mobs[entity] = (npc, x, y, agora)
                            self.radar_posicao_estimada = None
                        self.radar_mapa_estimado = chave
                        if self.radar_posicao is None or agora - self.radar_posicao[2] > 10:
                            self.radar_mapa_escolhido = chave
            elif tipo == "mob_remove":
                self.radar_mobs.pop(evento[1], None)
            if tipo in ("mob_spawn", "mob_remove"):
                self.radar_posicao_estimada = centro_radar_anunciado(
                    self.radar_mobs, self.radar_posicao_estimada, agora)

        def _atualizar_radar(self, catalogo=None):
            ui = getattr(self, "_radar_ui", None)
            if self.cfg.get("aba", "almas") != "almas" or not ui or not ui[0].winfo_exists():
                return
            if catalogo is None:
                catalogo = self.prog.catalogo_pets(self.nomes_pets,
                                                  self.cfg.get("tiers", [5, 25, 75]))
            agora = time.monotonic()
            especies = getattr(getattr(self, "leitor", None), "radar_mascotes", {})
            mapa = self.radar_mapa_escolhido
            pos_exata = (self.radar_posicao if self.radar_zona in RADAR_MAPAS[mapa][1]
                         and agora - self.radar_posicao[2] <= 10 else None) if self.radar_posicao else None
            pos_estimada = (self.radar_posicao_estimada
                            if self.radar_mapa_estimado == mapa and self.radar_posicao_estimada
                            and agora - self.radar_posicao_estimada[2] <= 60 else None)
            self._radar_pos_estimada_ativa = pos_exata is None and pos_estimada is not None
            self._radar_pos_exibicao = pos_exata or pos_estimada or (
                self.radar_posicao if self.radar_zona in RADAR_MAPAS[mapa][1] else None)
            if self._radar_mapa_visual != mapa or self._radar_pos_visual is None:
                self._radar_pos_visual = (self._radar_pos_exibicao[:2]
                                          if self._radar_pos_exibicao else None)
                self._radar_mapa_visual = mapa
                self._radar_assinatura = None
            linhas = linhas_radar(self.radar_mobs, pos_exata, catalogo,
                                  especies, agora)
            pos_mapa = self._radar_pos_exibicao
            raio_m = (RADAR_RAIO_ESTIMADO if self._radar_pos_estimada_ativa
                      else RADAR_RAIO_EXATO)
            conhecidos = locais_radar(self.radar_pontos.get(mapa, {}), pos_mapa,
                                      catalogo, especies, raio=raio_m)
            self._radar_cache = (catalogo, especies, conhecidos)
            self._desenhar_mapa_radar(catalogo, especies, conhecidos)
            for chave, botao in ui[4].items():
                cor = COR["ouro"] if chave == mapa else COR["suave"]
                if botao.cget("fg") != cor:
                    botao.configure(fg=cor)
            if not especies and hasattr(self, "leitor"):
                mensagem = "Catálogo do radar ausente. Atualize o aplicativo."
            elif self._radar_pos_estimada_ativa:
                mensagem = f"{len(linhas)} bichos vistos · área aproximada pelos monstros"
            elif not self.rede_sincronizada:
                mensagem = ("Progresso salvo; entre no personagem para confirmar."
                            if linhas else "Aguardando monstros próximos; entre no personagem para sincronizar.")
            elif self.radar_posicao is None:
                mensagem = "Mapa de locais conhecidos · aguardando sua posição no jogo."
            elif agora - self.radar_posicao[2] > 10:
                mensagem = "Última posição conhecida; use uma habilidade para atualizar."
            elif not linhas and conhecidos:
                mensagem = f"{len(conhecidos)} locais de farm próximos · monstros ainda não vistos"
            elif not linhas:
                mensagem = "Nenhum monstro pendente visto por perto."
            else:
                mensagem = f"{len(linhas)} monstro(s) visto(s) · distância aproximada"
            status = ui[1]
            if status.cget("text") != mensagem:
                status.configure(text=mensagem)
            exibidos = [("vivo", item) for item in linhas[:3]]
            if len(exibidos) < 3 and pos_mapa:
                vistos = {item[0] for item in linhas}
                for item in conhecidos:
                    if item[0] in vistos or any(e[1][0] == item[0] for e in exibidos):
                        continue
                    exibidos.append(("local", item))
                    if len(exibidos) == 3:
                        break
            for i, (row, titulo, detalhe) in enumerate(ui[2]):
                if i >= len(exibidos):
                    if row.winfo_manager():
                        row.pack_forget()
                    continue
                tipo, item = exibidos[i]
                monstro, pet, faltam, distancia, direcao = item[:5]
                prefixo = "●" if tipo == "vivo" else "•"
                local = (f"{direcao} ~{distancia:.0f} m" if distancia is not None
                         else "na área" if self._radar_pos_estimada_ativa
                         else "última posição")
                linha = f"{prefixo} {local}  {monstro}"
                sub = f"{pet} · faltam {faltam}" + (" · local conhecido" if tipo == "local" else "")
                if titulo.cget("text") != linha:
                    titulo.configure(text=linha)
                if detalhe.cget("text") != sub:
                    detalhe.configure(text=sub)
                if not row.winfo_manager():
                    row.pack(fill="x", padx=self.px(7), pady=(0, self.px(5)))

        def _montar_info(self):
            bg = COR["painel"]
            self._info = {}

            def bloco():
                f = tk.Frame(self.corpo, bg=bg)
                f.pack(fill="x", pady=(0, self.px(4)))
                return f

            def linha(pai, chave, rotulo, cor=COR["texto"], fonte=None):
                l = tk.Frame(pai, bg=bg)
                l.pack(fill="x", padx=self.px(8), pady=self.px(2))
                tk.Label(l, text=rotulo, fg=COR["suave"], bg=bg, font=self.f_nome).pack(side="left")
                v = tk.Label(l, text="", fg=cor, bg=bg, font=fonte or self.f_nome)
                v.pack(side="right")
                self._info[chave] = v

            b1 = bloco()
            tk.Frame(b1, bg=bg, height=self.px(3)).pack()
            linha(b1, "diario", "Reset di\u00e1rio (04:00)", COR["ouro"], self.fontes["relogio"])
            linha(b1, "semanal", "Reset semanal (qua 04:00)", COR["ouro"], self.fontes["relogio"])
            tk.Frame(b1, bg=bg, height=self.px(3)).pack()
            self._atualizar_info()

        def _atualizar_info(self):
            info = getattr(self, "_info", None)
            if not info:
                return
            import datetime as dt
            agora = dt.datetime.now()
            val = {
                "diario": formatar_longo((proximo_reset_diario(agora) - agora).total_seconds()),
                "semanal": formatar_longo((proximo_reset_semanal(agora) - agora).total_seconds()),
            }
            for k, v in val.items():
                if info[k].cget("text") != v:
                    info[k].configure(text=v)

        # ---------------- aba OD ----------------
        def _montar_od(self):
            bg = COR["painel"]
            self._od_ui = {}
            compacto = bool(self.cfg.get("compacto"))
            titulo_od = tk.Frame(self.corpo, bg=COR["fundo"])
            titulo_od.pack(fill="x", pady=(self.px(4), self.px(8)))
            tk.Label(titulo_od, text="ODs", fg=COR["alma"], bg=COR["fundo"],
                     font=self.fontes["titulo"]).pack(side="left")
            tk.Label(titulo_od, text="COLETA", fg=COR["suave"], bg=COR["fundo"],
                     font=self.f_peq).pack(side="right")
            if compacto:
                # grade 2x2 com nomes curtos (a cor do losango diz qual e)
                grade = tk.Frame(self.corpo, bg=COR["fundo"])
                grade.pack(fill="x")
                grade.columnconfigure(0, weight=1, uniform="od")
                grade.columnconfigure(1, weight=1, uniform="od")
            for i, tipo in enumerate(OD_TIPOS):
                if compacto:
                    f = tk.Frame(grade, bg=bg, highlightthickness=1,
                                 highlightbackground=COR["barra"])
                    f.grid(row=i // 2, column=i % 2, sticky="ew",
                           padx=(0, self.px(2)) if i % 2 == 0 else (self.px(2), 0),
                           pady=(0, self.px(3)))
                    l = tk.Frame(f, bg=bg)
                    l.pack(fill="x", padx=self.px(6), pady=self.px(3))
                    rotulo = {"OD": "Normal", "OD Fino": "Fine",
                              "OD Puro": "Pure", "OD Radiante": "Radiant"}[tipo]
                else:
                    f = tk.Frame(self.corpo, bg=bg, highlightthickness=1,
                                 highlightbackground=COR["barra"])
                    f.pack(fill="x", pady=(0, self.px(5)))
                    l = tk.Frame(f, bg=bg)
                    l.pack(fill="x", padx=self.px(8), pady=self.px(5))
                    rotulo = {"OD": "Odyle", "OD Fino": "Fine Odyle",
                              "OD Puro": "Pure Odyle", "OD Radiante": "Radiant Odyle"}[tipo]
                tk.Label(l, text="\u25c6", fg=OD_CORES[tipo], bg=bg,
                         font=self.fontes["botao"]).pack(side="left", padx=(0, self.px(4 if compacto else 6)))
                nome = tk.Label(l, text=rotulo, fg=COR["texto"], bg=bg, font=self.f_nome)
                nome.pack(side="left")
                self.botao(l, "+", lambda e, t=tipo: self.od_ajustar(t, +1),
                           fonte=self.fontes["botao_b"], dica=f"+1 {tipo}").pack(side="right")
                self.botao(l, "\u2212", lambda e, t=tipo: self.od_ajustar(t, -1),
                           fonte=self.fontes["botao_b"], dica=f"\u22121 {tipo}").pack(side="right")
                n = tk.Label(l, text="", fg=COR["alma"], bg=bg,
                             font=self.f_nome if compacto else self.fontes["titulo"],
                             cursor="hand2", width=4 if compacto else 6, anchor="e")
                n.pack(side="right", padx=(0, self.px(2 if compacto else 6)))
                n.bind("<Button-1>", lambda e, t=tipo: self.od_definir(t))
                self.dica(n, f"{tipo}: clique para digitar quantos voc\u00ea tem")
                for x in (f, l, nome, n):
                    x.bind("<Button-3>", lambda e: self.menu_od(e))
                self._od_ui[tipo] = (nome, n)
            barra = tk.Frame(self.corpo, bg=COR["fundo"])
            barra.pack(fill="x", pady=(self.px(5), self.px(5)))
            self.botao(barra, "\u21ba Zerar ODs", lambda e: self.od_zerar(),
                       fonte=self.f_peq, dica="Zerar os 4 contadores de OD (pergunta antes)").pack(side="right")
            # cronometro (para medir quanto OD voce faz por tempo)
            tk.Label(barra, text="\u23f1", fg=COR["suave"], bg=COR["fundo"],
                     font=self.f_peq).pack(side="left")
            self._lbl_crono = tk.Label(barra, text="", fg=COR["texto"], bg=COR["fundo"],
                                       font=self.fontes["relogio"])
            self._lbl_crono.pack(side="left", padx=(self.px(3), self.px(2)))
            self._b_crono = self.botao(barra, "\u25b6", lambda e: self.crono_alternar(),
                                       fonte=self.f_peq,
                                       dica=lambda: "Pausar o cron\u00f4metro" if self._crono().get("inicio")
                                       else "Come\u00e7ar o cron\u00f4metro")
            self._b_crono.pack(side="left")
            self.botao(barra, "\u21ba", lambda e: self.crono_zerar(), fonte=self.f_peq,
                       dica="Zerar o cron\u00f4metro").pack(side="left")
            self._atualizar_od()

        def _montar_materiais(self):
            from tkinter import ttk
            area = tk.Frame(self.corpo, bg=COR["fundo"])
            area.pack(fill="both", expand=True)
            cabecalho = tk.Frame(area, bg=COR["fundo"])
            cabecalho.pack(fill="x", pady=(self.px(3), self.px(7)))
            resumo = tk.Label(cabecalho, text="", fg=COR["ouro"], bg=COR["fundo"],
                              font=self.fontes["titulo"], justify="left")
            resumo.pack(side="left")
            tk.Label(cabecalho, text="COLETA", fg=COR["suave"], bg=COR["fundo"],
                     font=self.f_peq).pack(side="right")
            seletores = tk.Frame(area, bg=COR["fundo"])
            seletores.pack(fill="x", pady=(0, self.px(6)))
            grupos_materiais = ("Ore", "Gem", "Log", "Herb", "Ingredient")
            self._material_grupo = getattr(self, "_material_grupo", "Ore")
            if self._material_grupo not in grupos_materiais:
                self._material_grupo = "Ore"
            self._material_botoes = {}
            for grupo in grupos_materiais:
                botao = self.botao(seletores, grupo,
                                   lambda e, g=grupo: self.material_selecionar(g),
                                   fonte=self.f_peq)
                botao.configure(pady=self.px(4), padx=self.px(4),
                                highlightthickness=1, highlightbackground=COR["barra"])
                botao.bind("<Leave>", lambda e, g=grupo, b=botao:
                           b.configure(fg=MATERIAL_CORES[g] if g == self._material_grupo
                                       else COR["suave"]))
                self._material_botoes[grupo] = botao
            botoes = list(self._material_botoes.values())
            def ajustar_categorias(e):
                colunas = 5 if e.width >= self.px(440) else (3 if e.width >= self.px(300) else 2)
                if getattr(seletores, "_colunas", None) == colunas:
                    return
                seletores._colunas = colunas
                for coluna in range(len(botoes)):
                    seletores.columnconfigure(coluna, weight=1 if coluna < colunas else 0,
                                               uniform="categorias")
                for i, botao in enumerate(botoes):
                    botao.grid(row=i // colunas, column=i % colunas, sticky="ew",
                               padx=self.px(2), pady=self.px(2))
            seletores.bind("<Configure>", ajustar_categorias)
            caixa = tk.Frame(area, bg=COR["fundo"])
            caixa.pack(fill="both", expand=True)
            canvas = tk.Canvas(caixa, bg=COR["fundo"], highlightthickness=0,
                               height=self.px(110))
            estilo = ttk.Style(self.root)
            if estilo.theme_use() != "clam":
                estilo.theme_use("clam")
            estilo.configure("Materiais.Vertical.TScrollbar",
                             background=COR["barra"], troughcolor=COR["fundo"],
                             arrowcolor=COR["suave"], bordercolor=COR["fundo"],
                             lightcolor=COR["barra"], darkcolor=COR["barra"])
            estilo.map("Materiais.Vertical.TScrollbar",
                       background=[("active", COR["destaque"]),
                                   ("pressed", COR["destaque"])])
            barra = ttk.Scrollbar(caixa, orient="vertical", command=canvas.yview,
                                  style="Materiais.Vertical.TScrollbar")
            canvas.configure(yscrollcommand=barra.set)
            canvas.pack(side="left", fill="both", expand=True)
            canvas._barra_materiais = barra
            linhas = tk.Frame(canvas, bg=COR["fundo"])
            linhas.columnconfigure(0, weight=1, uniform="material")
            linhas.columnconfigure(1, weight=1, uniform="material")
            janela = canvas.create_window((0, 0), window=linhas, anchor="nw")
            linhas.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
            def ajustar_canvas(e):
                canvas.itemconfigure(janela, width=e.width)
                colunas = 1 if e.width < self.px(360) else 2
                if getattr(canvas, "_colunas_materiais", None) != colunas:
                    canvas._colunas_materiais = colunas
                    self._organizar_materiais()
            canvas.bind("<Configure>", ajustar_canvas)
            canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-int(e.delta / 120), "units"))
            self._materiais_ui = (resumo, linhas, {})
            rodape = tk.Frame(area, bg=COR["fundo"])
            rodape.pack(fill="x", pady=(self.px(5), 0))
            tk.Label(rodape, text="\u23f1", fg=COR["suave"], bg=COR["fundo"],
                     font=self.f_peq).pack(side="left")
            self._lbl_crono = tk.Label(rodape, text="", fg=COR["texto"], bg=COR["fundo"],
                                       font=self.fontes["relogio"])
            self._lbl_crono.pack(side="left", padx=(self.px(3), self.px(2)))
            self._b_crono = self.botao(rodape, "\u25b6", lambda e: self.crono_alternar(),
                                       fonte=self.f_peq,
                                       dica=lambda: "Pausar o cronômetro" if self._crono().get("inicio")
                                       else "Começar o cronômetro")
            self._b_crono.pack(side="left")
            self.botao(rodape, "\u21ba", lambda e: self.crono_zerar(), fonte=self.f_peq,
                       dica="Zerar o cronômetro").pack(side="left")
            zerar = self.botao(rodape, "\u21ba Zerar Materiais", lambda e: self.material_zerar(),
                               fonte=self.f_peq, dica="Zerar todos os materiais coletados")
            zerar.pack(side="right")
            rodape.bind("<Configure>", lambda e: zerar.configure(
                text="\u21ba Zerar" if e.width < self.px(350) else "\u21ba Zerar Materiais"))
            self._atualizar_materiais()

        def _organizar_materiais(self):
            ui = getattr(self, "_materiais_ui", None)
            if not ui or self.cfg.get("aba") != "materiais":
                return
            _, area, widgets = ui
            canvas = area.master
            colunas = getattr(canvas, "_colunas_materiais", 2)
            existentes = []
            from pet_protocol import GATHER_ITEM_TYPES
            for item, (grupo, nome) in GATHER_ITEM_TYPES.items():
                item_id = str(item)
                quantidade = self.prog.materiais.get(item_id, 0)
                if grupo == self._material_grupo and quantidade > 0 and item_id in widgets:
                    existentes.append((grupo, nome, item_id, quantidade))
            if self._material_grupo == "Ore":
                existentes.sort(key=lambda l: int(l[2]))
            else:
                existentes.sort(key=lambda l: l[1])
            existentes.sort(key=lambda l: -self.destaque.get("\u00a7material:" + l[2], 0))
            for indice, (_, _, item_id, _) in enumerate(existentes):
                frame = widgets[item_id][0].master.master
                posicao = (indice // colunas, indice % colunas)
                if getattr(frame, "_posicao_grade", None) != posicao:
                    frame.grid(row=posicao[0], column=posicao[1], sticky="nsew",
                               padx=self.px(2), pady=self.px(2))
                    frame._posicao_grade = posicao
            self._altura_materiais_chave = None
            self._ajustar_altura_materiais(len(existentes), canvas)

        def _atualizar_materiais(self):
            from pet_protocol import GATHER_ITEM_TYPES
            ui = getattr(self, "_materiais_ui", None)
            if not ui or self.cfg.get("aba") != "materiais":
                return
            resumo, area, widgets = ui
            grupos = ("Ore", "Gem", "Log", "Herb", "Ingredient")
            totais = {grupo: 0 for grupo in grupos}
            linhas = []
            for item, (grupo, nome) in GATHER_ITEM_TYPES.items():
                quantidade = self.prog.materiais.get(str(item), 0)
                totais[grupo] += quantidade
                if grupo == self._material_grupo and quantidade > 0:
                    linhas.append((grupo, nome, str(item), quantidade))
            texto = self._material_grupo
            if resumo.cget("text") != texto:
                resumo.configure(text=texto)
            cor_grupo = MATERIAL_CORES[self._material_grupo]
            if resumo.cget("fg") != cor_grupo:
                resumo.configure(fg=cor_grupo)
            self._atualizar_crono()
            for grupo, botao in self._material_botoes.items():
                cor = MATERIAL_CORES[grupo] if grupo == self._material_grupo else COR["suave"]
                if botao.cget("fg") != cor:
                    botao.configure(fg=cor)
                fundo = COR["barra"] if grupo == self._material_grupo else COR["fundo"]
                if botao.cget("bg") != fundo:
                    botao.configure(bg=fundo)
                borda = MATERIAL_CORES[grupo] if grupo == self._material_grupo else COR["barra"]
                if botao.cget("highlightbackground") != borda:
                    botao.configure(highlightbackground=borda)
            if self._material_grupo == "Ore":
                linhas.sort(key=lambda l: int(l[2]))
            else:
                linhas.sort(key=lambda l: l[1])
            linhas.sort(key=lambda l: -self.destaque.get("\u00a7material:" + l[2], 0))
            desejados = {item_id for _, _, item_id, _ in linhas}
            for item_id in set(widgets) - desejados:
                titulo, _ = widgets.pop(item_id)
                titulo.master.master.destroy()
            for grupo, nome, item_id, quantidade in linhas:
                if item_id not in widgets:
                    linha = tk.Frame(area, bg=COR["painel"],
                                     highlightthickness=1, highlightbackground=COR["barra"])
                    interno = tk.Frame(linha, bg=COR["painel"])
                    interno.pack(fill="x", padx=self.px(5), pady=self.px(3))
                    cor = (OD_CORES["OD Radiante"] if "Radiant" in nome else
                           OD_CORES["OD Puro"] if "Pure" in nome else
                           OD_CORES["OD Fino"] if "Fine" in nome or "Fresh" in nome
                           else OD_CORES["OD"])
                    tk.Frame(linha, bg=cor, width=self.px(2)).place(
                        x=0, y=0, relheight=1)
                    interno.columnconfigure(1, weight=1)
                    tk.Label(interno, text="\u25c6", fg=cor, bg=COR["painel"],
                             font=self.fontes["botao"]).grid(row=0, column=0, padx=(0, self.px(4)))
                    rotulo = ({610620013: "Normal", 610620014: "Fine",
                               610620015: "Pure", 610620016: "Radiant"}.get(int(item_id), nome)
                              if grupo == "Ore" else nome)
                    titulo = tk.Label(interno, text=rotulo, fg=COR["texto"], bg=COR["painel"],
                                      font=self.f_nome, anchor="w", justify="left")
                    titulo.grid(row=0, column=1, sticky="ew")
                    titulo.bind("<Configure>", lambda e, t=titulo:
                                t.configure(wraplength=max(self.px(40), e.width)))
                    self.botao(interno, "+", lambda e, i=item_id: self.material_ajustar(i, 1),
                               fonte=self.fontes["botao_b"]).grid(row=0, column=4)
                    self.botao(interno, "\u2212", lambda e, i=item_id: self.material_ajustar(i, -1),
                               fonte=self.fontes["botao_b"]).grid(row=0, column=3)
                    numero = tk.Label(interno, text="", fg=COR["alma"], bg=COR["painel"],
                                      font=self.f_nome, cursor="hand2", width=3, anchor="e")
                    numero.grid(row=0, column=2, padx=(self.px(4), self.px(2)))
                    numero.bind("<Button-1>", lambda e, i=item_id: self.material_definir(i))
                    self.dica(numero, f"{nome}: clique para digitar a quantidade")
                    widgets[item_id] = (titulo, numero)
                titulo, numero = widgets[item_id]
                valor = str(quantidade)
                if numero.cget("text") != valor:
                    numero.configure(text=valor)
                novo = time.time() - self.destaque.get("\u00a7material:" + item_id, 0) < 2.5
                cor = COR["novo"] if novo else COR["alma"]
                if numero.cget("fg") != cor:
                    numero.configure(fg=cor)
                cor_nome = COR["novo"] if novo else COR["texto"]
                if titulo.cget("fg") != cor_nome:
                    titulo.configure(fg=cor_nome)
            # Uma coluna na janela estreita; duas quando houver espaço.
            colunas = getattr(canvas := area.master, "_colunas_materiais", 2)
            for indice, (_, _, item_id, _) in enumerate(linhas):
                frame = widgets[item_id][0].master.master
                posicao = (indice // colunas, indice % colunas)
                if getattr(frame, "_posicao_grade", None) != posicao:
                    frame.grid(row=posicao[0], column=posicao[1], sticky="nsew",
                               padx=self.px(2), pady=self.px(2))
                    frame._posicao_grade = posicao
            barra = canvas._barra_materiais
            if len(linhas) > 4:
                if not barra.winfo_manager():
                    barra.pack(side="right", fill="y", before=canvas)
                canvas.configure(yscrollcommand=barra.set)
            else:
                if barra.winfo_manager():
                    barra.pack_forget()
                canvas.configure(yscrollcommand="")
                canvas.yview_moveto(0)
            self._ajustar_altura_materiais(len(linhas), canvas)

        def _ajustar_altura_materiais(self, quantidade, canvas):
            """Encolhe quando vazio e acrescenta altura a cada linha de cartões."""
            colunas = getattr(canvas, "_colunas_materiais", 2)
            chave = (self._material_grupo, quantidade, colunas)
            if getattr(self, "_altura_materiais_chave", None) == chave:
                return
            self._altura_materiais_chave = chave
            linhas = (quantidade + colunas - 1) // colunas
            canvas.configure(height=self.px(max(1, min(linhas, 2) * 34)))

            def aplicar():
                if self.cfg.get("aba") != "materiais":
                    return
                self.root.update_idletasks()
                desejada = max(self.px(185), self.root.winfo_reqheight())
                limite = self.root.winfo_screenheight() - self.root.winfo_y() - self.px(20)
                desejada = min(desejada, max(self.px(185), limite))
                if abs(self.root.winfo_height() - desejada) > self.px(2):
                    self.root.geometry(f"{self.root.winfo_width()}x{desejada}")
            self.root.after_idle(aplicar)

        def material_selecionar(self, grupo):
            if grupo == self._material_grupo:
                return
            self._material_grupo = grupo
            ui = getattr(self, "_materiais_ui", None)
            if ui:
                _, _, widgets = ui
                for titulo, _ in widgets.values():
                    titulo.master.master.destroy()
                widgets.clear()
            self._atualizar_materiais()

        def _trocar_coleta_suave(self, destino, grupo=None):
            """Transição curta entre ODs e categorias de materiais durante a coleta."""
            self._transicao_coleta_pendente = (destino, grupo)
            if getattr(self, "_transicao_coleta_ativa", False):
                return
            self._transicao_coleta_ativa = True
            base = float(self.cfg.get("opacidade", 1.0))

            def alfa(valor):
                try:
                    self.root.attributes("-alpha", max(0.35, min(1.0, valor)))
                except Exception:
                    pass

            def sair(passo=0):
                if passo < 3:
                    alfa(base * (1.0 - (passo + 1) * 0.045))
                    self.root.after(22, lambda: sair(passo + 1))
                    return
                alvo, grupo_alvo = self._transicao_coleta_pendente
                self._transicao_coleta_pendente = None
                if self.cfg.get("aba") in ("od", "materiais"):
                    if self.cfg.get("aba") != alvo:
                        self.selecionar_coleta(alvo)
                    if alvo == "materiais" and grupo_alvo:
                        self.material_selecionar(grupo_alvo)
                entrar(0)

            def entrar(passo=0):
                if passo < 3:
                    alfa(base * (0.865 + (passo + 1) * 0.045))
                    self.root.after(22, lambda: entrar(passo + 1))
                    return
                alfa(base)
                self._transicao_coleta_ativa = False
                pendente = self._transicao_coleta_pendente
                if pendente:
                    self._trocar_coleta_suave(*pendente)

            sair()

        def material_ajustar(self, item_id, delta):
            atual = self.prog.materiais.get(item_id, 0)
            self.prog.materiais[item_id] = max(0, atual + delta)
            self.prog.salvar()
            self._atualizar_materiais()

        def material_definir(self, item_id):
            from pet_protocol import GATHER_ITEM_TYPES
            nome = GATHER_ITEM_TYPES[int(item_id)][1]
            valor = self.dialogo(simpledialog.askinteger, APP_NOME, f"Quantos {nome} você coletou?",
                                 initialvalue=self.prog.materiais.get(item_id, 0), minvalue=0)
            if valor is not None:
                self.prog.materiais[item_id] = valor
                self.prog.salvar()
                self._atualizar_materiais()

        def material_zerar(self):
            if not self.dialogo(messagebox.askyesno, APP_NOME,
                                "Zerar todos os materiais coletados? Os ODs não serão alterados."):
                return
            self.prog.materiais.clear()
            self.prog.salvar()
            ui = getattr(self, "_materiais_ui", None)
            if ui:
                _, _, widgets = ui
                for titulo, _ in widgets.values():
                    titulo.master.master.destroy()
                widgets.clear()
            self._atualizar_materiais()
            registrar("MATERIAIS ZERADOS PELO USUARIO")

        def _atualizar_od(self):
            ui = getattr(self, "_od_ui", None)
            if not ui or self.cfg.get("aba") != "od":
                return
            self._atualizar_crono()
            self._atualizar_materiais()
            agora = time.time()
            for tipo, (nome, n) in ui.items():
                try:
                    if not n.winfo_exists():
                        return
                except Exception:
                    return
                txt = str(self.prog.od.get(tipo, 0))
                novo = agora - self.destaque.get("\u00a7od:" + tipo, 0) < 2.5
                cor = COR["novo"] if novo else COR["alma"]
                if n.cget("text") != txt:
                    n.configure(text=txt)
                if n.cget("fg") != cor:
                    n.configure(fg=cor)
                cn = COR["novo"] if novo else COR["texto"]
                if nome.cget("fg") != cn:
                    nome.configure(fg=cn)

        # ---------------- cronometro de farm (compartilhado entre as abas) ----------------
        def _crono(self):
            return self.cfg.setdefault("cronometro", {"acum": 0.0, "inicio": None})

        def crono_segundos(self):
            c = self._crono()
            return float(c.get("acum", 0)) + (time.time() - c["inicio"] if c.get("inicio") else 0)

        def crono_alternar(self):
            c = self._crono()
            if c.get("inicio"):
                c["acum"] = self.crono_segundos()
                c["inicio"] = None
            else:
                c["inicio"] = time.time()
            self.salvar_cfg()
            self._atualizar_crono()

        def crono_zerar(self):
            c = self._crono()
            c["acum"] = 0.0
            if c.get("inicio"):
                c["inicio"] = time.time()  # se estava rodando, continua do zero
            self.salvar_cfg()
            self._atualizar_crono()

        def _atualizar_crono(self):
            txt = formatar_tempo_farm(self.crono_segundos())
            rodando = bool(self._crono().get("inicio"))
            cor = COR["alma"] if rodando else COR["suave"]
            ic = "\u2016" if rodando else "\u25b6"
            pares = [(getattr(self, "_lbl_crono", None), getattr(self, "_b_crono", None)),
                     (getattr(self, "_lbl_crono_home", None),
                      getattr(self, "_b_crono_home", None))]
            for aba in getattr(self, "_abas_visuais", {}).values():
                pares.extend(((aba.get("_lbl_crono"), aba.get("_b_crono")),
                              (aba.get("_lbl_crono_home"), aba.get("_b_crono_home"))))
            vistos = set()
            for lbl, botao in pares:
                if lbl is None or id(lbl) in vistos:
                    continue
                vistos.add(id(lbl))
                try:
                    if not lbl.winfo_exists():
                        continue
                    if lbl.cget("text") != txt or lbl.cget("fg") != cor:
                        lbl.configure(text=txt, fg=cor)
                    if botao is not None and botao.winfo_exists() and botao.cget("text") != ic:
                        botao.configure(text=ic)
                except Exception:
                    continue

        def od_ajustar(self, tipo, n):
            self.prog.od[tipo] = max(0, self.prog.od.get(tipo, 0) + n)
            self.prog.salvar()
            self._atualizar_od()

        def od_definir(self, tipo):
            v = self.dialogo(simpledialog.askinteger, APP_NOME, f"Quantos {tipo} voc\u00ea tem?",
                             initialvalue=self.prog.od.get(tipo, 0), minvalue=0)
            if v is not None:
                self.prog.od[tipo] = v
                self.prog.salvar()
                registrar(f"EDITADO PELO USUARIO: {tipo} -> {v}")
                self._atualizar_od()

        def menu_od(self, e):
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Zerar os contadores de OD\u2026", command=self.od_zerar)
            self.abrir_menu(menu, e)

        def od_zerar(self):
            if self.dialogo(messagebox.askyesno, APP_NOME, "Zerar os 4 contadores de OD?"):
                for t in OD_TIPOS:
                    self.prog.od[t] = 0
                self.prog.salvar()
                registrar("OD ZERADO PELO USUARIO")
                self._atualizar_od()

        def atualizar_evento(self, agendar=True):
            """Relogio do proximo evento: mm:ss, a cada meio segundo. No ultimo
            minuto fica vermelho e piscando. So mexe nesses dois textos."""
            import datetime as dt
            lbl, rel = getattr(self, "_lbl_evento", None), getattr(self, "_lbl_relogio", None)
            if lbl is not None and rel is not None:
                try:
                    agora = dt.datetime.now()
                    info = getattr(self, "_ev_cache", None)
                    quando = getattr(self, "_ev_quando", None)
                    # recalcula quando o evento comeca/termina e a cada 20s
                    # (eventos juntos acabam em horas diferentes: Loja x Kairah)
                    if info is None or quando is None or agora < quando or \
                            (agora - quando).total_seconds() > 20 or \
                            agora >= (info[2] if info[3] else info[1]):
                        info = evento_info(self.cfg.get("eventos", []), agora)
                        self._ev_cache, self._ev_quando = info, agora
                    if info:
                        nomes, t, fim_ev, andando = info
                        if andando:
                            txt_n, txt_r, cor = f"\u25cf {nomes}", "agora", COR["ouro"]
                        else:
                            falta = (t - agora).total_seconds()
                            txt_n, txt_r = f"\u25b8 {nomes}", formatar_relogio(falta)
                            cor = COR["ouro"]
                            if falta <= 60:
                                # ultimo minuto: vermelho piscando (2x por segundo)
                                aceso = int(agora.timestamp() * 2) % 2 == 0
                                cor = COR["erro"] if aceso else COR["fundo"]
                        # nao deixa o texto alargar o contador
                        f_ev = self.fontes["evento"]
                        larg = self.px(int(170 * self._escala_letra()))
                        if f_ev.measure(txt_n) > larg:
                            while len(txt_n) > 3 and f_ev.measure(txt_n + "\u2026") > larg:
                                txt_n = txt_n[:-1]
                            txt_n = txt_n.rstrip(" +") + "\u2026"
                        if lbl.cget("text") != txt_n:
                            lbl.configure(text=txt_n)
                        if rel.cget("text") != txt_r:
                            rel.configure(text=txt_r)
                        if rel.cget("fg") != cor:
                            rel.configure(fg=cor)
                except Exception:
                    pass
            try:
                self._checar_aviso_som()
            except Exception:
                pass
            try:
                self._atualizar_crono()
                if self.cfg.get("aba") == "info":
                    self._atualizar_info()
                elif self.cfg.get("aba") == "od":
                    self._atualizar_od()
                elif self.cfg.get("aba") == "materiais":
                    self._atualizar_materiais()
            except Exception:
                pass
            if agendar:
                self.root.after(500, self.atualizar_evento)

        def _checar_aviso_som(self):
            """1 minuto antes de cada evento comecar: toca o aviso (uma vez)."""
            if not self.cfg.get("aviso_som", True):
                return
            import datetime as dt
            agora = dt.datetime.now()
            px = getattr(self, "_px_cache", None)
            feito = getattr(self, "_px_quando", None)
            # recalcula quando o evento chega ou se o relogio voltou (fuso, ajuste)
            if px is None or feito is None or agora >= px[1] or agora < feito:
                px = proximo_inicio(self.cfg.get("eventos", []), agora)
                self._px_cache, self._px_quando = px, agora
            if not px:
                return
            nomes, t = px
            falta = (t - agora).total_seconds()
            chave = (nomes, t)
            if 0 < falta <= 60 and chave != getattr(self, "_avisado", None):
                self._avisado = chave
                tocar_aviso()
                registrar(f"AVISO SONORO: {nomes} em {int(falta)}s")

        def mudar_cor(self, cor):
            antiga = COR["alma"]
            aplicar_cor_destaque(cor)
            self.cfg["cor_destaque"] = cor
            self.salvar_cfg()

            def recolorir(w):
                for op in ("fg", "bg", "highlightcolor", "insertbackground"):
                    try:
                        if str(w.cget(op)).lower() == antiga.lower():
                            w.configure(**{op: cor})
                    except Exception:
                        pass
                if isinstance(w, tk.Canvas):
                    for it in w.find_all():
                        try:
                            if str(w.itemcget(it, "fill")).lower() == antiga.lower():
                                w.itemconfigure(it, fill=cor)
                        except Exception:
                            pass
                for c in w.winfo_children():
                    recolorir(c)
            recolorir(self.root)
            self._estrutura = None
            self.render()
            self._pintar_som()
            self.atualizar_status()

        def mudar_volume(self, v):
            self.cfg["volume_aviso"] = v
            VOLUME_AVISO["v"] = v
            self.salvar_cfg()
            tocar_aviso()  # ja toca para voce ouvir como ficou

        def alternar_som(self):
            self.cfg["aviso_som"] = not self.cfg.get("aviso_som", True)
            self.salvar_cfg()
            self._pintar_som()
            if self.cfg["aviso_som"]:
                tocar_aviso()

        def _pintar_som(self):
            b = getattr(self, "b_som", None)
            if b is None:
                return
            if self.cfg.get("aviso_som", True):
                b.configure(fg=COR["alma"], font=self.fontes["botao"])
            else:
                b.configure(fg="#5a6070", font=self.fontes["som_mudo"])

        def _atualizar_linha(self, m, meta, w):
            feitas, falta_tier, n_tiers = estrelas(m["qtd"], self.cfg.get("tiers", [5, 25, 75]))
            completo = feitas >= n_tiers
            # contador e barra mostram a estrela atual (ex.: 3a estrela 70/75)
            _tiers = self.cfg.get("tiers", [5, 25, 75])
            est_t, faltam_t = tier_atual(m["qtd"], _tiers)
            tamanho_t = _tiers[est_t - 1]
            cor_barra = COR["ouro"] if completo else COR["alma"]
            novo = {
                "cont": (("\u2713 " if completo else "") +
                         f"{tamanho_t - faltam_t}/{tamanho_t}"),
                "falta": (f"{m['qtd']} no total · faltam {max(0, meta - m['qtd'])}"
                          if m["qtd"] < meta else f"{m['qtd']} no total · completo"),
            }
            # estrelas: douradas = completas; contorno dourado = a que esta
            # fazendo agora; cinza = ainda nao chegou
            for i, st in enumerate(w["estrelas"], 1):
                if i <= feitas:
                    est = ("cheia", 10)
                elif i == feitas + 1:
                    # a estrela atual vai enchendo (de 10 em 10%)
                    tam = _tiers[i - 1] if i - 1 < len(_tiers) else 1
                    est = ("atual", int(10 * (tam - faltam_t) / tam) if tam else 0)
                else:
                    est = ("vazia", 0)
                if st._estado != est:
                    st._estado = est
                    st.configure(image=self._img_estrela(est[0], est[1], st._bg))
            # so mexe no que mudou de verdade
            for chave, txt in novo.items():
                if chave in w and w[chave].cget("text") != txt:
                    w[chave].configure(text=txt)
            # acabou de ganhar alma: nome e numero ficam dourados por uns
            # segundos (so troca a cor, nao redesenha nada)
            t_alma = self.destaque.get(normalizar(m["nome"]), 0)
            resta = 2.5 - (time.time() - t_alma)
            cor_cont = COR["novo"] if resta > 0 else cor_barra
            cor_nome = COR["novo"] if resta > 0 else COR["texto"]
            if resta > 0:
                self.root.after(int(resta * 1000) + 50, self.render_em_breve)
            if w["cont"].cget("fg") != cor_cont:
                w["cont"].configure(fg=cor_cont)
            if w["nome"].cget("fg") != cor_nome:
                w["nome"].configure(fg=cor_nome)
            w["_ultima_qtd"] = m["qtd"]
            w["_ultimo_nome"] = m["nome"]
            w["_realce_ativo"] = resta > 0
            # nome comprido: cortado com '...'; quando ganha alma, desliza
            # para mostrar o nome inteiro
            if w.get("nome_txt") != m["nome"]:
                w["nome_txt"] = m["nome"]
                self._parar_letreiro(w)
                self._mostrar_nome(w, 0)
            if resta > 0 and t_alma != w.get("letreiro_de") and \
                    self.fontes["nome"].measure(m["nome"]) > w["nome_larg"]:
                w["letreiro_de"] = t_alma
                self._rodar_letreiro(w)
            cor_f = COR["ouro"] if completo else COR["suave"]
            if "falta" in w and w["falta"].cget("fg") != cor_f:
                w["falta"].configure(fg=cor_f)
            if "cv" not in w:
                return
            W, H = w["W"], w["H"]
            if w.get("larga"):
                W = max(1, w["cv"].winfo_width())  # compacto: barra da largura da linha
            frac = (tamanho_t - faltam_t) / tamanho_t if tamanho_t else 0
            w["cv"].coords(w["barra"], 0, 0, int(W * frac), H)
            w["cv"].itemconfigure(w["barra"], fill=cor_barra)
            if "caixa" not in w:
                return  # compacto: sem caixinha
            cx = w["caixa"]
            tenho_tier = tamanho_t - faltam_t
            if getattr(self, "editando", None) is not cx and cx.get() != str(tenho_tier):
                cx.delete(0, "end")
                cx.insert(0, str(tenho_tier))

        def _estrelas_na(self, topo, m, w, bg):
            tiers = self.cfg.get("tiers", [5, 25, 75])
            w["estrelas"] = []
            for i in range(1, len(tiers) + 1):
                st = tk.Label(topo, bg=bg, cursor="hand2", padx=self.px(2), bd=0)
                st._bg = bg
                st._estado = None
                st.pack(side="left", padx=(self.px(4) if i == 1 else 0, 0))
                st.bind("<Button-1>", lambda e, m=m, i=i: self.escolher_estrela(m, i))
                self.dica(st, f"Estou na {i}\u00aa estrela no jogo (clique para marcar)")
                w["estrelas"].append(st)

        def _img_estrela(self, estado, decimos, bg):
            S = max(12, int(self.fontes["estrela"].metrics("linespace") * 1.1))
            cache = self.__dict__.setdefault("_cache_estrelas", {})
            chave = (estado, decimos, S, bg)
            if chave not in cache:
                from PIL import ImageTk
                cache[chave] = ImageTk.PhotoImage(
                    imagem_estrela(estado, decimos / 10.0, S, bg), master=self.root)
            return cache[chave]

        # ---------------- nome com largura fixa + letreiro ----------------
        def _largura_nome(self):
            # do tamanho de um nome comprido comum: o contador nao muda de largura
            return self.fontes["nome"].measure("Esp\u00edrito do Vento Intermedi\u00e1rio") + self.px(2)

        def _caixa_nome(self, topo, w, bg):
            larg = self._largura_nome()
            caixa = tk.Frame(topo, bg=bg, width=larg,
                             height=self.fontes["nome"].metrics("linespace") + self.px(2))
            caixa.pack(side="left")
            caixa.pack_propagate(False)
            w["nome"] = tk.Label(caixa, text="", fg=COR["texto"], bg=bg, font=self.f_nome,
                                 anchor="w", padx=0)
            w["nome"].pack(side="left", fill="both", expand=True)
            w["nome_larg"] = larg
            w["nome_caixa"] = caixa

        def _cabe(self, texto, larg, reticencias=True):
            """Corta o texto para caber em larg pixels (com '...' no fim)."""
            f = self.fontes["nome"]
            if f.measure(texto) <= larg:
                return texto
            fim = "\u2026" if reticencias else ""
            n = len(texto)
            while n > 1 and f.measure(texto[:n].rstrip() + fim) > larg:
                n -= 1
            return texto[:n].rstrip() + fim

        def _mostrar_nome(self, w, inicio):
            txt = w.get("nome_txt", "")
            if inicio <= 0:
                vis = self._cabe(txt, w["nome_larg"])
            else:
                vis = self._cabe(txt[inicio:], w["nome_larg"], reticencias=False)
            if w["nome"].cget("text") != vis:
                w["nome"].configure(text=vis)

        def _parar_letreiro(self, w):
            if w.get("letreiro"):
                try:
                    self.root.after_cancel(w["letreiro"])
                except Exception:
                    pass
                w["letreiro"] = None

        def _rodar_letreiro(self, w):
            """Desliza o nome ate aparecer o final, segura um pouco e volta."""
            self._parar_letreiro(w)
            txt = w.get("nome_txt", "")
            f, larg = self.fontes["nome"], w["nome_larg"]
            ultimo = 0
            while ultimo < len(txt) - 1 and f.measure(txt[ultimo:]) > larg:
                ultimo += 1

            def passo(i):
                try:
                    if not w["nome"].winfo_exists():
                        return
                except Exception:
                    return
                if i > ultimo:
                    w["letreiro"] = self.root.after(1500, lambda: (self._mostrar_nome(w, 0),
                                                                  w.__setitem__("letreiro", None)))
                    return
                self._mostrar_nome(w, i)
                w["letreiro"] = self.root.after(110 if i else 500, lambda: passo(i + 1))
            passo(0)

        def _linha_compacta(self, m, meta, pai=None):
            """Uma linha so: nome, estrelas, numero (clique para editar), - + e x.
            Renomear e juntar ficam no botao direito."""
            bg = COR["painel"]
            f = tk.Frame(pai or self.corpo, bg=bg)
            f.pack(fill="x", pady=(0, self.px(2)))
            topo = tk.Frame(f, bg=bg)
            topo.pack(fill="x", padx=self.px(6), pady=(self.px(2), self.px(1)))
            w = {"frame": f}
            self._caixa_nome(topo, w, bg)
            self._estrelas_na(topo, m, w, bg)
            self.botao(topo, "\u2715", lambda e, m=m: self.fechar_monstro(m),
                       cor=COR["erro"], fonte=self.fontes["fechar"],
                       dica="Tirar este bicho da lista").pack(side="right")
            self.botao(topo, "+", lambda e, m=m: self.ajustar(m, +1),
                       fonte=self.fontes["botao_b"], dica="+1 alma").pack(side="right")
            self.botao(topo, "\u2212", lambda e, m=m: self.ajustar(m, -1),
                       fonte=self.fontes["botao_b"], dica="\u22121 alma").pack(side="right")
            w["cont"] = tk.Label(topo, text="", fg=COR["alma"], bg=bg, font=self.f_nome,
                                 width=8, anchor="e", cursor="hand2")
            w["cont"].pack(side="right")

            # clicar no numero: vira uma caixinha com o 'tenho' da estrela
            def editar(e, m=m, w=w, topo=topo):
                if getattr(self, "editando", None) is not None:
                    return "break"
                tiers = self.cfg.get("tiers", [5, 25, 75])
                est, faltam = tier_atual(m["qtd"], tiers)
                cx = tk.Entry(topo, width=4, justify="center", font=self.f_nome,
                              bg=COR["barra"], fg=COR["texto"], insertbackground=COR["texto"],
                              relief="flat", highlightthickness=1,
                              highlightbackground="#3a4050", highlightcolor=COR["alma"])
                cx.insert(0, str(tiers[est - 1] - faltam))
                tam = tk.Label(topo, text=f"/{tiers[est - 1]}", fg=COR["suave"], bg=bg, font=self.f_nome)
                w["cont"].pack_forget()
                tam.pack(side="right")
                cx.pack(side="right")
                self.editando = cx
                self.pegar_teclado()
                cx.focus_force()
                cx.select_range(0, "end")

                def fim(salvar):
                    if getattr(self, "editando", None) is not cx:
                        return
                    self.editando = None
                    txt = re.sub(r"[^0-9]", "", cx.get())
                    cx.destroy()
                    tam.destroy()
                    w["cont"].pack(side="right")
                    if salvar and txt:
                        self.definir_tenho(m, int(txt))
                    self.root.focus_set()
                    self.render_em_breve()
                cx.bind("<Return>", lambda e: fim(True))
                cx.bind("<KP_Enter>", lambda e: fim(True))
                cx.bind("<FocusOut>", lambda e: fim(True))
                cx.bind("<Escape>", lambda e: fim(False))
                return "break"
            w["cont"].bind("<Button-1>", editar)
            self.dica(w["cont"], "Almas nesta estrela. Clique para digitar quantas voc\u00ea tem")
            # linha fininha de progresso da estrela atual
            H = max(2, self.px(2))
            w["cv"] = tk.Canvas(f, height=H, width=1, bg=COR["barra"], highlightthickness=0, bd=0)
            w["cv"].pack(fill="x", padx=self.px(6), pady=(0, self.px(3)))
            w["barra"] = w["cv"].create_rectangle(0, 0, 0, H, fill=COR["alma"], width=0)
            w["W"], w["H"], w["larga"] = 0, H, True
            w["cv"].bind("<Configure>", lambda e, m=m, w=w: self._atualizar_linha(m, int(self.cfg["meta"]), w))
            for x in (f, topo, w["nome"], w["nome_caixa"], w["cont"]):
                x.bind("<Button-3>", lambda e, m=m: self.menu_monstro(e, m))
            self._atualizar_linha(m, meta, w)
            return w

        def _linha(self, m, meta, pai=None):
            if self.cfg.get("compacto"):
                return self._linha_compacta(m, meta, pai)
            bg = COR["painel"]
            f = tk.Frame(pai or self.corpo, bg=bg)
            f.pack(fill="x", pady=(0, self.px(4)))
            topo = tk.Frame(f, bg=bg)
            topo.pack(fill="x", padx=self.px(8), pady=(self.px(5), 0))
            w = {"frame": f}
            self._caixa_nome(topo, w, bg)
            # estrelas clicaveis: clique na estrela em que voce esta no jogo
            self._estrelas_na(topo, m, w, bg)
            self.botao(topo, "\u2715", lambda e, m=m: self.fechar_monstro(m),
                       cor=COR["erro"], fonte=self.fontes["fechar"],
                       dica="Tirar este bicho da lista").pack(side="right")
            self.botao(topo, "+", lambda e, m=m: self.ajustar(m, +1),
                       fonte=self.fontes["botao_b"], dica="+1 alma").pack(side="right")
            self.botao(topo, "\u2212", lambda e, m=m: self.ajustar(m, -1),
                       fonte=self.fontes["botao_b"], dica="\u22121 alma").pack(side="right")
            w["cont"] = tk.Label(topo, text="", fg=COR["alma"], bg=bg, font=self.f_nome,
                                 width=8, anchor="e")
            w["cont"].pack(side="right", padx=(0, self.px(4)))

            base = tk.Frame(f, bg=bg)
            base.pack(fill="x", padx=self.px(8), pady=(self.px(3), self.px(6)))
            W, H = self.px(int(130 * self._escala_letra())), self.px(6)
            w["W"], w["H"] = W, H
            w["cv"] = tk.Canvas(base, width=W, height=H, bg=COR["barra"], highlightthickness=0)
            w["cv"].pack(side="left")
            w["barra"] = w["cv"].create_rectangle(0, 0, 0, H, fill=COR["alma"], width=0)
            w["falta"] = tk.Label(base, text="", fg=COR["suave"], bg=bg, font=self.f_peq)
            w["falta"].pack(side="left", padx=(self.px(7), 0))
            caixa = tk.Entry(base, width=4, justify="center", font=self.f_nome,
                             bg=COR["barra"], fg=COR["texto"],
                             insertbackground=COR["texto"], relief="flat",
                             highlightthickness=1, highlightbackground="#3a4050",
                             highlightcolor=COR["alma"])
            caixa.pack(side="right")
            w["caixa"] = caixa
            tk.Label(base, text="tenho", fg=COR["suave"], bg=bg,
                     font=self.f_peq).pack(side="right", padx=(self.px(6), self.px(3)))

            def comecar(e, cx=caixa):
                self.editando = cx
                self.pegar_teclado()
                cx.focus_force()
                cx.select_range(0, "end")
                return "break"

            def confirmar(e=None, cx=caixa, m=m):
                if getattr(self, "editando", None) is not cx:
                    return
                self.editando = None
                txt = re.sub(r"[^0-9]", "", cx.get())
                if txt:
                    self.definir_tenho(m, int(txt))
                self.root.focus_set()
                self.render_em_breve()

            def cancelar(e=None, cx=caixa):
                self.editando = None
                self.root.focus_set()
                self.render_em_breve()

            caixa.bind("<Button-1>", comecar)
            self.dica(caixa, "Quantas almas voc\u00ea tem nesta estrela (digite e aperte Enter)")
            caixa.bind("<Return>", confirmar)
            caixa.bind("<KP_Enter>", confirmar)
            caixa.bind("<FocusOut>", confirmar)
            caixa.bind("<Escape>", cancelar)

            for x in (f, topo, base, w["nome"], w["nome_caixa"]):
                x.bind("<Button-3>", lambda e, m=m: self.menu_monstro(e, m))
            self._atualizar_linha(m, meta, w)
            return w

        def definir_tenho(self, m, tenho):
            """'tenho N' na estrela atual (ou na que acabou de clicar)."""
            tiers = self.cfg.get("tiers", [5, 25, 75])
            estrela = getattr(self, "_estrela_escolhida", {}).pop(id(m), None) \
                or tier_atual(m["qtd"], tiers)[0]
            tamanho = tiers[estrela - 1]
            tenho = max(0, min(int(tenho), tamanho))
            total = total_por_tier(estrela, tamanho - tenho, tiers)
            self.prog.definir(m, total)
            registrar(f"EDITADO PELO USUARIO: {m['nome']} estrela {estrela} tenho {tenho} -> {total}")

        def perguntar_tenho(self, m):
            tiers = self.cfg.get("tiers", [5, 25, 75])
            est, faltam = tier_atual(m["qtd"], tiers)
            tam = tiers[est - 1]
            v = self.dialogo(simpledialog.askinteger, APP_NOME,
                             f"{m['nome']}\nQuantas almas voc\u00ea tem na {est}\u00aa estrela (de {tam})?",
                             initialvalue=tam - faltam, minvalue=0, maxvalue=tam)
            if v is not None:
                self.definir_tenho(m, v)
                self.render()

        def _escala_letra(self):
            return {"pequena": 0.85, "normal": 1.0, "grande": 1.2,
                    "muito grande": 1.45}.get(self.cfg.get("tamanho_letra", "normal"), 1.0)

        def _aplicar_tamanho_fontes(self):
            esc = self._escala_letra()
            for k, (fam, tam, peso) in self._fontes_base.items():
                self.fontes[k].configure(size=max(6, int(round(tam * esc))))
            if "som_mudo" in self.fontes:
                self.fontes["som_mudo"].configure(size=self.fontes["botao"].cget("size"))

        def mudar_tamanho_letra(self, nome):
            if self.cfg.get("aba") == "info":
                self.alternar_aba()  # volta para medir a largura certa
            self.cfg["tamanho_letra"] = nome
            self.salvar_cfg()
            self._aplicar_tamanho_fontes()
            self._estrutura = None  # remonta as linhas (barra acompanha a letra)
            self.render()

        def alternar_compacto(self):
            if getattr(self, "editando", None) is not None:
                return
            self.cfg["compacto"] = not self.cfg.get("compacto")
            self.salvar_cfg()
            if getattr(self, "b_modo", None) is not None:
                self.b_modo.configure(text="\u25a4" if self.cfg["compacto"] else "\u2261")
            self._estrutura = None
            self.render()

        def escolher_estrela(self, m, i):
            """Clicou na estrela i: o monstro passa a estar no comeco dessa
            estrela e a caixinha 'faltam' ja fica pronta para digitar."""
            tiers = self.cfg.get("tiers", [5, 25, 75])
            atual, faltam = tier_atual(m["qtd"], tiers)
            if i != atual or m["qtd"] >= sum(tiers):
                self.prog.definir(m, sum(tiers[:i - 1]))
                registrar(f"EDITADO PELO USUARIO: {m['nome']} estrela {i}")
            self._estrela_escolhida = getattr(self, "_estrela_escolhida", {})
            self._estrela_escolhida[id(m)] = i
            self.render()
            w = getattr(self, "_ui", {}).get(id(m))
            if w:
                cx = w["caixa"]
                self.editando = cx
                self.pegar_teclado()
                cx.focus_force()
                cx.select_range(0, "end")

        def fechar_monstro(self, m):
            """Tira o monstro do overlay na hora (sem perguntar)."""
            if m in self.prog.monstros:
                registrar(f"REMOVIDO PELO USUARIO: {m['nome']} ({m['qtd']})")
                self.prog.remover(m)
                w = getattr(self, "_ui", {}).get(id(m))
                estrutura = getattr(self, "_estrutura", None)
                if not w or not estrutura or getattr(self, "editando", None) is not None:
                    self.render()
                    return
                # A remocao nao altera as demais linhas: conserva os widgets,
                # a rolagem e o foco para nao piscar a janela inteira.
                self._parar_letreiro(w)
                w["frame"].destroy()
                del self._ui[id(m)]
                restantes = [x for x in self.prog.monstros if id(x) in self._ui]
                vazio = ""
                if not restantes:
                    vazio = ("Aguardando as almas recebidas pelo AION 2 via Npcap…"
                             if self.cfg.get("fonte_leitura") == "npcap" else
                             ("Procurando o chat do jogo… deixe o chat visível."
                              if not self.cfg.get("regiao") else
                              "Chat encontrado. Aguardando a primeira alma…"))
                    tk.Label(self.corpo, text=vazio, fg=COR["suave"],
                             bg=COR["fundo"], font=self.f_peq,
                             wraplength=self.px(240), justify="left").pack(
                                 anchor="w", pady=self.px(4), before=self._lbl_rodape.master)
                self._estrutura = (tuple(i for i in estrutura[0] if i != id(m)),
                                   estrutura[1], vazio, *estrutura[3:])
                if self._rolagem:
                    if restantes:
                        limite = max(1, int(self.cfg.get("max_linhas", 6)))
                        self._ajustar_rolagem(restantes[:limite])
                    else:
                        self._rolagem[0].master.destroy()
                        self._rolagem = None
                if self._lbl_rodape:
                    meta = int(self.cfg["meta"])
                    concluidos = sum(x["qtd"] >= meta for x in restantes)
                    rodape = f"{concluidos}/{len(restantes)} concluídos"
                    self._lbl_rodape.configure(text=rodape)

        def ajustar(self, m, n):
            m["qtd"] = max(0, m["qtd"] + n)
            m["atualizado"] = time.time()
            self.prog.salvar()
            self.render()

        def definir_qtd(self, m):
            v = self.dialogo(simpledialog.askinteger, APP_NOME,
                             f"Quantas almas de {m['nome']} voc\u00ea j\u00e1 tem?",
                             initialvalue=m["qtd"], minvalue=0)
            if v is not None:
                self.prog.definir(m, v)
                self.render()

        # ---------------- menus ----------------
        def menu_monstro(self, e, m):
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="Tenho nesta estrela\u2026", command=lambda: self.perguntar_tenho(m))
            menu.add_command(label="+1 alma", command=lambda: self.ajustar(m, +1))
            menu.add_command(label="\u22121 alma", command=lambda: self.ajustar(m, -1))
            menu.add_separator()
            menu.add_command(label="Definir quantidade total\u2026", command=lambda: self.definir_qtd(m))
            menu.add_command(label="Renomear\u2026", command=lambda: self.renomear(m))
            outros = [o for o in self.prog.monstros if o is not m]
            if outros:
                sub = tk.Menu(menu, tearoff=0)
                for o in sorted(outros, key=lambda o: o["nome"]):
                    sub.add_command(label=o["nome"],
                                    command=lambda o=o: self.juntar(m, o))
                menu.add_cascade(label="Juntar com (erro de leitura)", menu=sub)
            menu.add_separator()
            menu.add_command(label="Remover", command=lambda: self.remover(m))
            self.abrir_menu(menu, e)

        def renomear(self, m):
            v = self.dialogo(simpledialog.askstring, APP_NOME, "Novo nome:",
                             initialvalue=m["nome"])
            if v and v.strip():
                self.prog.renomear(m, v.strip())
                self.render()

        def juntar(self, origem, destino):
            if self.dialogo(messagebox.askyesno, APP_NOME,
                            f"Somar \u201c{origem['nome']}\u201d em \u201c{destino['nome']}\u201d?\n"
                            "Leituras futuras com esse nome v\u00e3o direto para o destino."):
                self.prog.juntar(origem, destino)
                self.render()

        def remover(self, m):
            if self.dialogo(messagebox.askyesno, APP_NOME, f"Remover {m['nome']}?"):
                self.prog.remover(m)
                self.render()

        def menu_principal(self, e):
            menu = tk.Menu(self.root, tearoff=0)
            if self.cfg.get("fonte_leitura") == "npcap":
                menu.add_command(label="Leitura do jogo: Npcap", state="disabled")
                menu.add_command(label="Ler dados atuais (reentrar no personagem)",
                                 command=self.pedir_sincronizacao)
            else:
                menu.add_command(label="Detectar o chat automaticamente", command=self.detectar_chat)
                menu.add_command(label="Selecionar \u00e1rea do chat na m\u00e3o\u2026", command=self.selecionar_regiao)
                menu.add_command(label="Ver leitura / ajustar padr\u00f5es\u2026", command=self.abrir_debug)
            menu.add_separator()
            menu.add_command(label="Adicionar monstro\u2026", command=self.adicionar)
            sub = tk.Menu(menu, tearoff=0)
            for v in (1.0, 0.9, 0.8, 0.7, 0.6):
                sub.add_command(label=f"{int(v*100)}%", command=lambda v=v: self.opacidade(v))
            menu.add_cascade(label="Opacidade", menu=sub)
            sub_cor = tk.Menu(menu, tearoff=0)
            atual_cor = COR["alma"].lower()
            for nome, cor in CORES_DESTAQUE:
                sub_cor.add_command(label=("\u2713 " if cor.lower() == atual_cor else "    ") + "\u25cf " + nome,
                                    foreground=cor, activeforeground=cor,
                                    command=lambda c=cor: self.mudar_cor(c))
            menu.add_cascade(label="Cor de destaque", menu=sub_cor)
            sub_letra = tk.Menu(menu, tearoff=0)
            atual = self.cfg.get("tamanho_letra", "normal")
            for nome in ("pequena", "normal", "grande", "muito grande"):
                sub_letra.add_command(label=("\u2713 " if nome == atual else "    ") + nome.capitalize(),
                                      command=lambda n=nome: self.mudar_tamanho_letra(n))
            menu.add_cascade(label="Tamanho da letra", menu=sub_letra)

            menu.add_checkbutton(label="Som 1 minuto antes dos eventos",
                                 onvalue=True, offvalue=False,
                                 variable=tk.BooleanVar(value=self.cfg.get("aviso_som", True)),
                                 command=self.alternar_som)
            sub_vol = tk.Menu(menu, tearoff=0)
            vol = float(self.cfg.get("volume_aviso", 0.5))
            for nome, v in (("M\u00ednimo", 0.05), ("Muito baixo", 0.12), ("Baixo", 0.25),
                            ("M\u00e9dio", 0.5), ("Alto", 0.75), ("M\u00e1ximo", 1.0)):
                sub_vol.add_command(label=("\u2713 " if abs(v - vol) < 0.01 else "    ") + nome,
                                    command=lambda v=v: self.mudar_volume(v))
            menu.add_cascade(label="Volume do aviso", menu=sub_vol)
            menu.add_checkbutton(label="Aparecer na stream (OBS/Discord)",
                                 onvalue=True, offvalue=False,
                                 variable=tk.BooleanVar(value=not self.cfg.get("esconder_da_captura", False)),
                                 command=self.alternar_stream)
            menu.add_separator()
            menu.add_command(label="Zerar todos os contadores\u2026", command=self.zerar)
            menu.add_command(label="Remover todos os monstros\u2026", command=self.remover_todos)
            menu.add_command(label="Abrir pasta dos dados", command=self.abrir_pasta)
            menu.add_command(label="Gerar relat\u00f3rio para suporte", command=self.gerar_relatorio)
            menu.add_command(label=f"Procurar atualiza\u00e7\u00e3o (vers\u00e3o {VERSAO})\u2026",
                             command=self.procurar_atualizacao)
            menu.add_separator()
            if self.cfg.get("fonte_leitura") != "npcap":
                menu.add_checkbutton(label="Captura DirectX (use se o chat n\u00e3o aparecer na leitura)",
                                     onvalue=True, offvalue=False,
                                     variable=tk.BooleanVar(value=self.cfg.get("captura") == "directx"),
                                     command=self.alternar_captura)
            menu.add_command(label="Sair", command=self.sair)
            self.abrir_menu(menu, e)

        def alternar_concluidos(self):
            self.cfg["mostrar_concluidos"] = not self.cfg.get("mostrar_concluidos")
            self.salvar_cfg()
            self.render()

        def opacidade(self, v):
            self.cfg["opacidade"] = v
            self.root.attributes("-alpha", v)
            self.salvar_cfg()

        def adicionar(self):
            nome = self.dialogo(simpledialog.askstring, APP_NOME, "Nome do monstro:")
            if not nome or not nome.strip():
                return
            qtd = self.dialogo(simpledialog.askinteger, APP_NOME,
                               "Quantas almas voc\u00ea j\u00e1 tem?", initialvalue=0, minvalue=0)
            m = self.prog.somar(nome.strip(), 0)
            if m["nome"] == nome.strip():
                m["fixo"] = True
            self.prog.definir(m, qtd or 0)
            self.render()

        def mudar_meta(self):
            atual = " / ".join(str(t) for t in self.cfg["tiers"])
            v = self.dialogo(simpledialog.askstring, APP_NOME,
                             "Quantas almas cada estrela precisa no jogo?\n"
                             "(1\u00aa / 2\u00aa / 3\u00aa estrela, separadas por barra)",
                             initialvalue=atual)
            if not v:
                return
            nums = [int(x) for x in re.findall(r"\d+", v)]
            if not nums or any(n <= 0 for n in nums):
                self.dialogo(messagebox.showerror, APP_NOME, "Use n\u00fameros, por exemplo: 5 / 25 / 75")
                return
            # mantem a estrela e o 'tenho' de cada monstro ao mudar os tamanhos
            antigos = self.cfg["tiers"]
            for m in self.prog.monstros:
                est, falta = tier_atual(m["qtd"], antigos)
                tenho = antigos[est - 1] - falta
                est = min(est, len(nums))
                m["qtd"] = sum(nums[:est - 1]) + min(tenho, nums[est - 1])
            self.prog.salvar()
            self.cfg["tiers"] = nums
            self.cfg["meta"] = sum(nums)
            registrar(f"ESTRELAS: {nums}")
            self.salvar_cfg()
            self._estrutura = None  # numero de estrelas pode mudar: remonta a lista
            self.render()

        def zerar(self):
            if self.dialogo(messagebox.askyesno, APP_NOME,
                            "Zerar TODOS os contadores? Isso n\u00e3o pode ser desfeito."):
                self.prog.zerar_tudo()
                self.render()

        def remover_todos(self):
            n = len(self.prog.monstros)
            if not n:
                return
            if self.dialogo(messagebox.askyesno, APP_NOME,
                            f"Tirar TODOS os {n} monstros da lista?\n\n"
                            "Uma c\u00f3pia fica guardada na pasta dos dados "
                            "(progresso_antes_de_limpar.json)."):
                self.prog.remover_todos()
                self._ui = {}
                registrar(f"REMOVIDOS TODOS PELO USUARIO ({n})")
                self.render()

        def _clique_atualizar(self):
            if getattr(self, "_versao_no_disco", None):
                self.prog.salvar()
                self.reiniciar_programa()
            else:
                self.procurar_atualizacao()

        def vigiar_arquivo(self):
            """O arquivo do programa foi trocado por uma versao nova (pelo
            GitHub ou na mao) com ele aberto? Avisa para reabrir."""
            try:
                with open(os.path.abspath(__file__), encoding="utf-8") as f:
                    m = re.search(r'^VERSAO\s*=\s*"([^"]+)"', f.read(4000), re.M)
                if m and versao_tupla(m.group(1)) > versao_tupla(VERSAO):
                    if getattr(self, "_versao_no_disco", None) != m.group(1):
                        self._versao_no_disco = m.group(1)
                        self.lbl_atualizar.configure(text=f"\u27f3 reabrir ({m.group(1)})")
                        registrar(f"ARQUIVO NOVO no disco: {m.group(1)}")
            except Exception:
                pass
            self.root.after(20000, self.vigiar_arquivo)

        def checar_atualizacao_em_segundo_plano(self):
            """Ao abrir: olha no GitHub se tem versao nova (sem perguntar nada)."""
            def tarefa():
                try:
                    v, _ = versao_remota()
                    if versao_tupla(v) > versao_tupla(VERSAO) and \
                            not getattr(self, "_versao_no_disco", None):
                        self.root.after(0, lambda: self.lbl_atualizar.configure(
                            text=f"\u2b06 {v}"))
                        registrar(f"ATUALIZACAO disponivel: {v}")
                except Exception as e:
                    registrar(f"ATUALIZACAO: nao consegui verificar ({e})")
            threading.Thread(target=tarefa, daemon=True).start()

        def procurar_atualizacao(self):
            self.status_extra = "procurando atualiza\u00e7\u00e3o\u2026"
            self.atualizar_status()

            def tarefa():
                try:
                    v, dados = versao_remota()
                    self.root.after(0, lambda: self._resposta_atualizacao(v, dados, None))
                except Exception as e:
                    self.root.after(0, lambda: self._resposta_atualizacao(None, None, e))
            threading.Thread(target=tarefa, daemon=True).start()

        def _resposta_atualizacao(self, v, dados, erro):
            self.status_extra = None
            self.atualizar_status()
            if erro is not None:
                self.dialogo(messagebox.showerror, APP_NOME,
                             f"N\u00e3o consegui verificar a atualiza\u00e7\u00e3o.\n\n{erro}\n\n"
                             "Confira a internet e tente de novo.")
                return
            if versao_tupla(v) <= versao_tupla(VERSAO):
                self.lbl_atualizar.configure(text="")
                self.dialogo(messagebox.showinfo, APP_NOME,
                             f"Voc\u00ea j\u00e1 est\u00e1 na vers\u00e3o mais nova ({VERSAO}).")
                return
            if not self.dialogo(messagebox.askyesno, APP_NOME,
                                f"Tem vers\u00e3o nova: {v} (voc\u00ea est\u00e1 na {VERSAO}).\n\n"
                                "Atualizar agora? O contador fecha e abre de novo sozinho, "
                                "e o seu progresso continua salvo."):
                return
            try:
                self.prog.salvar()
                self.salvar_cfg()
                mudou_req = aplicar_atualizacao(dados, BASE)
                registrar(f"ATUALIZADO: {VERSAO} -> {v}")
                if mudou_req:
                    import subprocess
                    exe = sys.executable
                    if exe.lower().endswith("pythonw.exe"):
                        exe = exe[:-len("pythonw.exe")] + "python.exe"
                    subprocess.run([exe, "-m", "pip", "install", "--target", BASE,
                                    "--upgrade", "-q", "--disable-pip-version-check", "-r",
                                    os.path.join(BASE, "requirements.txt")],
                                   capture_output=True, timeout=600,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self._atualizar_registro_windows(v)
            except Exception as e:
                registrar(f"ATUALIZACAO falhou: {e}")
                self.dialogo(messagebox.showerror, APP_NOME,
                             f"A atualiza\u00e7\u00e3o falhou e nada foi trocado.\n\n{e}")
                return
            self.reiniciar_programa()

        def _atualizar_registro_windows(self, v):
            if sys.platform != "win32":
                return
            try:
                import winreg
                caminho = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\ContadorDeAlmas"
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, caminho, 0, winreg.KEY_SET_VALUE) as k:
                    winreg.SetValueEx(k, "DisplayVersion", 0, winreg.REG_SZ, v)
            except Exception:
                pass  # versao da Area de Trabalho (sem instalador): nao tem registro

        def reiniciar_programa(self):
            """Abre a versao nova (que espera esta fechar) e fecha esta."""
            import subprocess
            script = os.path.join(BASE, "contador_almas.py")
            flags = 0
            if sys.platform == "win32":
                flags = getattr(subprocess, "DETACHED_PROCESS", 0x8) | \
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
            subprocess.Popen([sys.executable, script, "--esperar", str(os.getpid())],
                             cwd=BASE, close_fds=True, creationflags=flags)
            self.sair()

        def alternar_captura(self):
            if self.cfg.get("captura") == "directx":
                self.cfg["captura"] = "normal"
                self.salvar_cfg()
                registrar("CAPTURA: normal (escolhida no menu)")
                return
            try:
                import dxcam  # noqa: F401
            except Exception:
                if not self.dialogo(messagebox.askyesno, APP_NOME,
                                    "A captura DirectX precisa de um complemento (dxcam).\n"
                                    "Instalar agora? Leva alguns segundos."):
                    return
                self.status_extra = "instalando captura DirectX\u2026"
                self.atualizar_status()
                threading.Thread(target=self._instalar_dxcam, daemon=True).start()
                return
            self._ligar_directx()

        def _instalar_dxcam(self):
            import subprocess
            exe = sys.executable
            if exe.lower().endswith("pythonw.exe"):
                exe = exe[:-len("pythonw.exe")] + "python.exe"
            try:
                r = subprocess.run([exe, "-m", "pip", "install", "--user", "-q",
                                    "--disable-pip-version-check", "dxcam"],
                                   capture_output=True, text=True, timeout=600,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                ok = r.returncode == 0
                registrar(f"INSTALAR dxcam: codigo {r.returncode} {r.stderr[-300:]}")
            except Exception as e:
                ok = False
                registrar(f"INSTALAR dxcam: falhou {e}")
            self.root.after(0, lambda: self._fim_instalar_dxcam(ok))

        def _fim_instalar_dxcam(self, ok):
            self.status_extra = None
            self.atualizar_status()
            if ok:
                import importlib, site
                try:
                    importlib.invalidate_caches()
                    p = site.getusersitepackages()
                    if p not in sys.path:
                        sys.path.append(p)
                except Exception:
                    pass
                self._ligar_directx()
            else:
                self.dialogo(messagebox.showerror, APP_NOME,
                             "N\u00e3o consegui instalar o complemento da captura DirectX.\n"
                             "Gere o relat\u00f3rio de suporte (menu \u2699) e mande para quem est\u00e1 ajudando.")

        def _ligar_directx(self):
            self.cfg["captura"] = "directx"
            self.salvar_cfg()
            registrar("CAPTURA: DirectX (escolhida no menu)")
            self.rastreador.reiniciar()
            self.dialogo(messagebox.showinfo, APP_NOME,
                         "Captura DirectX ligada.\n\nAbra \u2699 \u2192 Ver leitura e confira se o chat "
                         "aparece na imagem da esquerda. Se n\u00e3o achar o chat, use "
                         "\u2699 \u2192 Detectar o chat automaticamente.")

        def gerar_relatorio(self):
            """Junta log, capturas e configuracao num .zip na Area de Trabalho,
            para mandar a quem esta ajudando."""
            import zipfile, platform
            try:
                leitor_imagem = self.leitor_imagem()
                if leitor_imagem.ultima:
                    leitor_imagem.ultima[0].save(os.path.join(BASE, "captura_original.png"))
                    leitor_imagem.ultima[1].save(os.path.join(BASE, "captura_tratada.png"))
                # print da tela inteira (onde o chat deveria estar)
                try:
                    import mss
                    from PIL import Image
                    with abrir_mss(mss) as sct:
                        for i, mon in enumerate(sct.monitors[1:], 1):
                            g = sct.grab(mon)
                            im = Image.frombytes("RGB", g.size, g.bgra, "raw", "BGRX")
                            im.thumbnail((1600, 1600))
                            im.save(os.path.join(BASE, f"tela_monitor{i}.jpg"), quality=80)
                except Exception as e:
                    registrar(f"RELATORIO: sem print da tela ({e})")
                registrar(f"RELATORIO: python {sys.version.split()[0]}, {platform.platform()}, "
                          f"motor={self.motor_nome}, regiao={self.cfg.get('regiao')}, "
                          f"monstros={len(self.prog.monstros)}")
                desk = os.path.join(os.path.expanduser("~"), "Desktop")
                try:
                    import ctypes.wintypes
                    buf = ctypes.create_unicode_buffer(260)
                    ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf)
                    if buf.value:
                        desk = buf.value
                except Exception:
                    pass
                destino = os.path.join(desk, "ContadorDeAlmas-relatorio.zip")
                with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
                    for nome in ("diagnostico.log", "diagnostico.log.old", "pet_overlay.log", "config.json",
                                 "captura_original.png", "captura_tratada.png",
                                 "tela_monitor1.jpg", "tela_monitor2.jpg", "tela_monitor3.jpg"):
                        cam = os.path.join(BASE, nome)
                        if os.path.exists(cam):
                            z.write(cam, nome)
                self.dialogo(messagebox.showinfo, APP_NOME,
                             "Relat\u00f3rio salvo na \u00c1rea de Trabalho:\n"
                             "ContadorDeAlmas-relatorio.zip\n\nMande esse arquivo para quem est\u00e1 te ajudando.")
            except Exception as e:
                self.dialogo(messagebox.showerror, APP_NOME, f"N\u00e3o consegui gerar o relat\u00f3rio: {e}")

        def abrir_pasta(self):
            try:
                os.startfile(BASE)
            except Exception:
                pass

        def _boas_vindas(self):
            self.dialogo(messagebox.showinfo, APP_NOME,
                         "Primeiro passo: abra o jogo e mate um monstro que d\u00e9 alma, "
                         "para a mensagem aparecer no chat.\n\n"
                         "Depois clique em \u2699 \u2192 Selecionar \u00e1rea do chat e "
                         "arraste um ret\u00e2ngulo s\u00f3 em volta das linhas do chat.")

        # ---------------- selecao de area ----------------
        def selecionar_regiao(self):
            """Tira um print da tela, mostra numa janela e o usuario desenha o
            retangulo em cima do print. Converte direto para as coordenadas da
            captura, entao funciona com 2 monitores e qualquer escala do Windows."""
            import mss
            from PIL import Image, ImageTk
            self.root.withdraw()
            self.root.update()
            time.sleep(0.25)
            try:
                with abrir_mss(mss) as s:
                    v = s.monitors[0]
                    shot = s.grab(v)
                print_tela = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
            finally:
                self.root.deiconify()
                self.root.attributes("-topmost", True)

            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            fator = min((sw * 0.95) / print_tela.width, (sh * 0.85) / print_tela.height, 1.0)
            mostrar = print_tela.resize((max(1, int(print_tela.width * fator)),
                                         max(1, int(print_tela.height * fator))), Image.LANCZOS)

            sel = tk.Toplevel(self.root)
            sel.title("Selecione o chat")
            sel.attributes("-topmost", True)
            sel.configure(bg=COR["fundo"])
            tk.Label(sel, text="Arraste um ret\u00e2ngulo em volta das \u00faltimas linhas do chat "
                               "(onde aparece \u201cObteve Pontos da Alma\u2026\u201d).  Esc cancela.",
                     fg=COR["texto"], bg=COR["fundo"], font=self.f_nome).pack(padx=8, pady=6)
            foto = ImageTk.PhotoImage(mostrar)
            cv = tk.Canvas(sel, width=mostrar.width, height=mostrar.height,
                           highlightthickness=0, cursor="crosshair")
            cv.pack(padx=8, pady=(0, 8))
            cv.create_image(0, 0, image=foto, anchor="nw")
            cv._foto = foto
            sel.geometry("+0+0")
            estado = {"ini": None, "ret": None}

            def ini(e):
                estado["ini"] = (e.x, e.y)
                estado["ret"] = cv.create_rectangle(e.x, e.y, e.x, e.y,
                                                    outline="#ff3355", width=2)

            def mov(e):
                if estado["ini"]:
                    x0, y0 = estado["ini"]
                    cv.coords(estado["ret"], x0, y0, e.x, e.y)

            def fim(e):
                if not estado["ini"]:
                    return
                x0, y0 = estado["ini"]
                x1, y1 = e.x, e.y
                estado["ini"] = None
                l = int(min(x0, x1) / fator)
                t = int(min(y0, y1) / fator)
                w = int(abs(x1 - x0) / fator)
                h = int(abs(y1 - y0) / fator)
                if w < 30 or h < 12:
                    return
                sel.destroy()
                self.cfg["regiao"] = {"left": v["left"] + l, "top": v["top"] + t,
                                      "width": w, "height": h}
                self.erro_od = None
                self.status_extra = None
                registrar(f"AREA ESCOLHIDA NA MAO: {self.cfg['regiao']}")
                self.salvar_cfg()
                self.rastreador.reiniciar()
                self.atualizar_status()
                self.render()
                self.abrir_debug()

            cv.bind("<ButtonPress-1>", ini)
            cv.bind("<B1-Motion>", mov)
            cv.bind("<ButtonRelease-1>", fim)
            sel.bind("<Escape>", lambda e: sel.destroy())
            sel.focus_force()

        # ---------------- janela de leitura ----------------
        def abrir_debug(self):
            if self.debug and self.debug.winfo_exists():
                self.debug.lift()
                return
            d = tk.Toplevel(self.root)
            d.title("Leitura do OCR")
            d.attributes("-topmost", True)
            d.configure(bg=COR["fundo"])
            d.geometry(f"{self.px(560)}x{self.px(680)}")
            self.debug = d
            self.root.after(200, lambda: self.esconder_da_captura(d))
            self.lbl_motor = tk.Label(d, text="", fg=COR["suave"], bg=COR["fundo"],
                                      font=self.f_peq, anchor="w", justify="left")
            self.lbl_motor.pack(fill="x", padx=8, pady=(8, 2))
            tk.Label(d, text="\u00c1rea capturada agora (esquerda) e como o OCR v\u00ea (direita):",
                     fg=COR["texto"], bg=COR["fundo"], font=self.f_nome,
                     anchor="w").pack(fill="x", padx=8)
            prev = tk.Frame(d, bg=COR["fundo"])
            prev.pack(fill="x", padx=8, pady=4)
            self.lbl_prev1 = tk.Label(prev, bg=COR["painel"])
            self.lbl_prev1.pack(side="left")
            self.lbl_prev2 = tk.Label(prev, bg=COR["painel"])
            self.lbl_prev2.pack(side="left", padx=(6, 0))
            tk.Label(d, text="O que o OCR est\u00e1 lendo agora  (\u2713 = reconhecida como alma, \u2192 = contada)",
                     fg=COR["texto"], bg=COR["fundo"], font=self.f_nome,
                     anchor="w").pack(fill="x", padx=8)
            self.txt_leitura = tk.Text(d, height=10, bg=COR["painel"], fg=COR["texto"],
                                       font=("Consolas", 9), relief="flat", wrap="none")
            self.txt_leitura.pack(fill="both", expand=True, padx=8, pady=4)
            tk.Label(d, text="Padr\u00f5es (um por linha; o nome do monstro fica em (?P<nome>...)):",
                     fg=COR["texto"], bg=COR["fundo"], font=self.f_nome,
                     anchor="w").pack(fill="x", padx=8, pady=(6, 0))
            self.txt_padroes = tk.Text(d, height=5, bg=COR["painel"], fg=COR["texto"],
                                       insertbackground=COR["texto"],
                                       font=("Consolas", 9), relief="flat", wrap="none")
            self.txt_padroes.pack(fill="x", padx=8, pady=4)
            self.txt_padroes.insert("1.0", "\n".join(self.cfg["padroes"]))
            bar = tk.Frame(d, bg=COR["fundo"])
            bar.pack(fill="x", padx=8, pady=(0, 8))
            tk.Button(bar, text="Salvar padr\u00f5es", command=self.salvar_padroes).pack(side="left")
            tk.Button(bar, text="Restaurar padr\u00e3o", command=self.restaurar_padroes).pack(side="left", padx=6)
            tk.Button(bar, text="Salvar imagem capturada", command=self.salvar_captura).pack(side="right")
            self.mostrar_debug(self.ultimas_linhas, [])

        def salvar_padroes(self):
            linhas = [l.strip() for l in self.txt_padroes.get("1.0", "end").splitlines() if l.strip()]
            ex = Extrator(linhas)
            if ex.erros:
                self.dialogo(messagebox.showerror, APP_NOME,
                             "Padr\u00e3o inv\u00e1lido:\n" + "\n".join(ex.erros))
                return
            self.cfg["padroes"] = linhas
            self.salvar_cfg()
            self.rastreador.extrator = ex
            self.mostrar_debug(self.ultimas_linhas, [])

        def salvar_captura(self):
            leitor_imagem = self.leitor_imagem()
            if not leitor_imagem.ultima:
                self.dialogo(messagebox.showinfo, APP_NOME, "Nada capturado ainda.")
                return
            bruta, tratada = leitor_imagem.ultima
            bruta.save(os.path.join(BASE, "captura_original.png"))
            tratada.save(os.path.join(BASE, "captura_tratada.png"))
            self.abrir_pasta()

        def restaurar_padroes(self):
            self.txt_padroes.delete("1.0", "end")
            self.txt_padroes.insert("1.0", "\n".join(PADROES_PADRAO))
            self.salvar_padroes()

        def atualizar_previa(self):
            par = self.leitor_imagem().ultima if hasattr(self, "leitor") else None
            if not par or not hasattr(self, "lbl_prev1"):
                return
            from PIL import ImageTk
            larg = self.px(260)
            fotos = []
            for img, lbl in zip(par, (self.lbl_prev1, self.lbl_prev2)):
                f = min(larg / img.width, self.px(220) / img.height)
                im = img.resize((max(1, int(img.width * f)), max(1, int(img.height * f))))
                foto = ImageTk.PhotoImage(im)
                lbl.configure(image=foto)
                lbl._foto = foto

        def mostrar_debug(self, linhas, contados):
            if not (self.debug and self.debug.winfo_exists()):
                return
            info = f"Motor: {self.motor_nome}"
            if self.leitor_ocr:
                info += f" · OD: {self.motor_ocr_nome}"
            if self.erro:
                info += f"\nERRO: {self.erro}"
            if self.erro_od:
                info += f"\nERRO OD: {self.erro_od}"
            reg = self.cfg.get("regiao")
            info += f"\n\u00c1rea: {reg if reg else 'n\u00e3o escolhida'}"
            self.lbl_motor.configure(text=info)
            self.atualizar_previa()
            self.txt_leitura.delete("1.0", "end")
            restantes = list(contados)
            for l in linhas:
                nome = self.rastreador.extrator.nome_em(l)
                marca = "   "
                if nome:
                    marca = " \u2713 "
                    if nome in restantes:
                        restantes.remove(nome)
                        marca = "\u2192\u2713 "
                extra = f"   [{nome}]" if nome else ""
                self.txt_leitura.insert("end", f"{marca}{l}{extra}\n")
            if not linhas:
                self.txt_leitura.insert("end", "(nada lido ainda)")

        # ---------------- loop ----------------
        def consumir(self):
            coleta_alterada = False
            ultimo_grupo = None
            destino_coleta = None
            if self.leitor_ocr:
                while True:
                    try:
                        tipo_od, dado_od = self.leitor_ocr.fila.get_nowait()
                    except queue.Empty:
                        break
                    if tipo_od == "linhas":
                        self.ao_ler(dado_od[0], None, dado_od[1], somente_od=True)
                    elif tipo_od == "linhas_pos":
                        self.ao_ler([t for t, _, _ in dado_od[0]], dado_od[0],
                                    dado_od[1], somente_od=True)
                    elif tipo_od == "motor":
                        self.motor_ocr_nome = dado_od
                    elif tipo_od == "regiao":
                        if dado_od:
                            self.cfg["regiao"] = dado_od
                            self.salvar_cfg()
                            self.rastreador.reiniciar()
                            self.erro_od = None
                            registrar(f"ÁREA DO CHAT PARA OD: {dado_od}")
                        else:
                            self.erro_od = "chat não localizado"
                            registrar("OD: chat não localizado; selecione a área do chat no menu")
                        self.atualizar_status()
                        self.mostrar_debug(self.ultimas_linhas, [])
                    elif tipo_od == "erro":
                        self.erro_od = str(dado_od)
                        registrar("ERRO OCR OD: " + self.erro_od)
                        self.atualizar_status()
                    elif tipo_od == "aviso":
                        registrar("AVISO OCR OD: " + str(dado_od))
            limite_fila = time.perf_counter() + 0.008
            try:
                while time.perf_counter() < limite_fila:
                    tipo, dado = self.leitor.fila.get_nowait()
                    if tipo == "linhas":
                        self.ao_ler(dado[0], None, dado[1])
                    elif tipo == "linhas_pos":
                        self.ao_ler([t for t, _, _ in dado[0]], dado[0], dado[1])
                    elif tipo == "repetido":
                        pass
                    elif tipo == "alma_rede":
                        self.ao_alma_rede(*dado)
                    elif tipo in ("abyss_points", "kinah_gain"):
                        quantidade = int(dado)
                        if quantidade > 0:
                            if tipo == "abyss_points":
                                self.pontos_abismo += quantidade
                                nome = "pontos"
                                total = self.pontos_abismo
                            else:
                                self.kinah_abismo += quantidade
                                nome = "Kinah"
                                total = self.kinah_abismo
                            self._atualizar_abismo()
                            registrar(f"NPCAP: +{quantidade} {nome} no Abismo (sessão: {total})")
                    elif tipo == "od_gain":
                        od_tipo, quantidade = dado
                        quantidade = int(quantidade)
                        if od_tipo in OD_TIPOS and quantidade > 0:
                            self.prog.od[od_tipo] = self.prog.od.get(od_tipo, 0) + quantidade
                            coleta_alterada = True
                            destino_coleta = "od"
                            self.destaque["\u00a7od:" + od_tipo] = time.time()
                            registrar(f"NPCAP: +{quantidade} {od_tipo}")
                    elif tipo == "gather_gain":
                        item_id, quantidade = dado
                        from pet_protocol import GATHER_ITEM_TYPES
                        quantidade = int(quantidade)
                        if item_id in GATHER_ITEM_TYPES and quantidade > 0:
                            chave = str(item_id)
                            self.prog.materiais[chave] = self.prog.materiais.get(chave, 0) + quantidade
                            coleta_alterada = True
                            destino_coleta = "materiais"
                            self.destaque["\u00a7material:" + chave] = time.time()
                            ultimo_grupo = GATHER_ITEM_TYPES[item_id][0]
                            registrar(f"NPCAP: +{quantidade} {GATHER_ITEM_TYPES[item_id][1]}")
                    elif tipo == "dps_party":
                        names = list(dict.fromkeys(str(n) for n in dado if n))
                        if names != self.dps_party:
                            self.dps_party = names
                            self.dps_damage = {n: self.dps_damage.get(n, 0) for n in names}
                            if self.cfg.get("aba") == "dps":
                                self._estrutura = None
                                self.render_em_breve()
                    elif tipo == "dps_damage":
                        name, amount = dado
                        amount = int(amount)
                        now = time.monotonic()
                        if self.dps_last_hit is not None and now - self.dps_last_hit >= 10:
                            self.dps_damage = {n: 0 for n in self.dps_party}
                            self.dps_started = now
                        if self.dps_started is None:
                            self.dps_started = now
                        self.dps_last_hit = now
                        self.dps_damage[name] = self.dps_damage.get(name, 0) + max(0, amount)
                        self._atualizar_dps()
                    elif tipo == "dps_reset":
                        self.dps_zerar()
                    elif tipo == "boss_health":
                        self.boss_health = (max(0, int(dado[0])), max(0, int(dado[1])))
                        self._atualizar_dps()
                    elif tipo == "estado_rede":
                        # Um estado de conta decodificado é uma confirmação mais forte
                        # que o sinal transitório de abertura da conexão TCP.
                        self.rede_conectada = True
                        self.rede_pronta = True
                        self.sincronizacao_pendente = False
                        assinatura = assinatura_estado_pets(dado[0], dado[1])
                        self._assinatura_estado_atual = assinatura
                        assinatura_salva = self.cfg.get("personagem_estado")
                        candidato = self._personagem_candidato
                        if candidato and self._assinatura_personagem_candidato == assinatura:
                            self.personagem = candidato
                            self._personagem_candidato = ""
                            self._assinatura_personagem_candidato = None
                            self.cfg["personagem"] = candidato
                            self.cfg["personagem_estado"] = assinatura
                            self._atualizar_personagem()
                            registrar("NPCAP: personagem confirmado pelo estado da conta: " + candidato)
                        elif candidato:
                            registrar("NPCAP: estado atual diferente do personagem salvo; aguardando nome do jogo")
                            self._personagem_candidato = ""
                            self._assinatura_personagem_candidato = None
                            self.personagem = ""
                            self.cfg["personagem"] = ""
                            self.cfg.pop("personagem_estado", None)
                            self._atualizar_personagem()
                        elif self.personagem and not assinatura_salva:
                            self.cfg["personagem_estado"] = assinatura
                        self.salvar_cfg()
                        estado_anterior = dict(self.prog.pet_estado)
                        mudou = self.prog.sincronizar_pets(*dado, self.leitor.nomes)
                        completados = [self.leitor.nomes.get(str(pid), f"Pet #{pid}")
                                       for pid, total in self.prog.pet_estado.items()
                                       if total >= 105 and estado_anterior.get(pid, 0) < 105]
                        if completados:
                            self.root.bell()
                            registrar("PET COMPLETO: " + ", ".join(completados))
                        self.prog.salvar()  # inclui a data da fotografia, mesmo sem mudanças
                        if mudou:
                            self.render_em_breve()
                        self.rede_sincronizada = True
                        registrar(f"NPCAP: progresso sincronizado ({len(dado[0])} níveis, "
                                  f"{len(dado[1])} contadores)")
                        self.atualizar_status()
                    elif tipo == "personagem_rede":
                        # Se o nome veio do jogo, a captura e o decoder já estão prontos.
                        self.rede_conectada = True
                        self.rede_pronta = True
                        self.sincronizacao_pendente = False
                        self._personagem_candidato = ""
                        self._assinatura_personagem_candidato = None
                        if dado != self.personagem:
                            self.personagem = dado
                            self.cfg["personagem"] = dado
                            if self._assinatura_estado_atual:
                                self.cfg["personagem_estado"] = self._assinatura_estado_atual
                            self.salvar_cfg()
                            self._atualizar_personagem()
                            registrar("NPCAP: personagem identificado: " + dado)
                        self.atualizar_status()
                    elif tipo == "conexao_rede":
                        self.rede_conectada = bool(dado)
                        if not dado:
                            self.rede_pronta = False
                            self.rede_sincronizada = False
                            self._assinatura_estado_atual = None
                        self.atualizar_status()
                    elif tipo == "rede_pronta":
                        self.rede_pronta = True
                        self.atualizar_status()
                    elif tipo == "motor":
                        registrar("MOTOR: " + str(dado))
                        self.motor_nome = dado
                        self.atualizar_status()
                    elif tipo == "erro":
                        registrar("ERRO LEITURA: " + str(dado))
                        self.erro = dado
                        self.atualizar_status()
                        self.dialogo(messagebox.showerror, APP_NOME, dado +
                                     ("\n\nVerifique se o Npcap está instalado."
                                      if self.cfg.get("fonte_leitura") == "npcap" else
                                      "\n\nVerifique se o Tesseract está instalado."))
                    elif tipo == "aviso":
                        agora_av = time.time()
                        if agora_av - getattr(self, "_ult_aviso", 0) > 10 or \
                                str(dado) != getattr(self, "_txt_aviso", ""):
                            registrar("AVISO: " + str(dado))
                            self._ult_aviso, self._txt_aviso = agora_av, str(dado)
                        self.motor_nome = dado
                        self.mostrar_debug(self.ultimas_linhas, [])  # previa nao trava
                    elif tipo == "status":
                        self.status_extra = "procurando o chat…"
                        self.atualizar_status()
                    elif tipo == "regiao":
                        registrar(f"DETECCAO DO CHAT: {dado}")
                        if dado:
                            self.cfg["regiao"] = dado
                            self.salvar_cfg()
                            self.rastreador.reiniciar()
                            self.status_extra = None
                        else:
                            self.status_extra = ("chat não achado – deixe o chat "
                                                 "visível" if not self.cfg.get("regiao") else None)
                        self.atualizar_status()
                        self.render()
                        self.mostrar_debug(self.ultimas_linhas, [])
            except queue.Empty:
                pass
            if coleta_alterada:
                self.prog.salvar()
                aba = self.cfg.get("aba")
                if aba in ("od", "materiais") and destino_coleta:
                    trocar = (aba != destino_coleta or
                              (destino_coleta == "materiais" and ultimo_grupo is not None and
                               ultimo_grupo != self._material_grupo))
                    if trocar:
                        self._trocar_coleta_suave(destino_coleta, ultimo_grupo)
                    elif aba == "od":
                        self._atualizar_od()
                    else:
                        self._atualizar_materiais()
                        if getattr(self, "_materiais_ui", None):
                            self._materiais_ui[1].master.yview_moveto(0)
            self.root.after(10 if not self.leitor.fila.empty() else 50, self.consumir)

        def ao_alma_rede(self, pet_id, quantidade):
            try:
                quantidade = int(quantidade)
                if not 1 <= quantidade <= 100:
                    return
                nome = self.leitor.nomes.get(str(pet_id), f"Alma #{pet_id}")
                monstro = self.prog.somar_pet(pet_id, nome, quantidade)
                self.prog.salvar()
                self.destaque[normalizar(monstro["nome"])] = time.time()
                self._rolar_para = monstro
                registrar(f"NPCAP: {nome} x{quantidade} (pet {pet_id})")
                self.render_em_breve()
            except Exception as e:
                registrar(f"ERRO ao registrar alma da rede: {e}")

        def _atualizar_abismo(self):
            pontos = f"{self.pontos_abismo:,}".replace(",", ".")
            kinah = f"{self.kinah_abismo:,}".replace(",", ".")
            if getattr(self, "_lbl_abismo", None):
                try:
                    self._lbl_abismo.configure(
                        text=f"Pontos: +{pontos}   ·   Kinah: +{kinah}")
                except tk.TclError:
                    pass

        def ao_ler(self, linhas, itens=None, t0=None, somente_od=False):
            self.ultimas_linhas = linhas
            if t0:
                # mede o atraso (captura -> resultado) para o diagnostico
                atraso = time.time() - t0
                self._atrasos = (getattr(self, "_atrasos", []) + [atraso])[-60:]
                if time.time() - getattr(self, "_ult_med", 0) > 30:
                    self._ult_med = time.time()
                    m = sum(self._atrasos) / len(self._atrasos)
                    registrar(f"VELOCIDADE: atraso medio {m:.2f}s ({len(self._atrasos)} leituras)")
            if itens is not None:
                nomes = self.rastreador.quadro_pos(itens, t0)
            else:
                nomes = self.rastreador.quadro(linhas, t0)
            if somente_od:
                nomes = [n for n in nomes if n.startswith("\u00a7OD:") or
                         n.startswith("\u00a7AP:")]
            for n in nomes:
                if n.startswith("\u00a7OD:"):
                    tipo = n[4:]
                    self.prog.od[tipo] = self.prog.od.get(tipo, 0) + 1
                    self.destaque["\u00a7od:" + tipo] = time.time()
                    continue
                m = self.prog.somar(n, 1, salvar=False)
                self.destaque[normalizar(m["nome"])] = time.time()
                self._rolar_para = m
            if nomes:
                self.prog.salvar()  # grava uma vez so, mesmo com varias almas
            # diagnostico: captura a cada 10s e registro do que leu/contou
            agora = time.time()
            leitor_imagem = self.leitor_imagem()
            if agora - getattr(self, "_ultimo_save", 0) > 10 and leitor_imagem.ultima:
                self._ultimo_save = agora
                try:
                    leitor_imagem.ultima[0].save(os.path.join(BASE, "captura_original.png"))
                    leitor_imagem.ultima[1].save(os.path.join(BASE, "captura_tratada.png"))
                except Exception:
                    pass
            almas = [l for l in linhas if self.rastreador.extrator.nome_em(l) or od_em(l)]
            if almas or nomes:
                registrar("LEU " + " || ".join(almas) + "  =>  CONTOU " + str(nomes))
            if nomes:
                self.render_em_breve()
            self.mostrar_debug(linhas, nomes)

        def render_em_breve(self):
            """Junta varios redesenhos seguidos em um so (matanca em massa)."""
            if getattr(self, "_render_agendado", False):
                return
            self._render_agendado = True

            def fazer():
                self._render_agendado = False
                self.render()
                m = getattr(self, "_rolar_para", None)
                if m is not None:
                    self._rolar_para = None
                    self.root.after(30, lambda: self.mostrar_linha(m))
            self.root.after(120, fazer)

        def sair(self):
            self.leitor.parar.set()
            if self.leitor_ocr:
                self.leitor_ocr.parar.set()
            c = self._crono()
            if c.get("inicio"):
                c["acum"] = self.crono_segundos()
                c["inicio"] = None
            if not getattr(self, "minimizado", False):
                self.cfg["posicao"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.salvar_cfg()
            self.prog.salvar()
            self.root.destroy()

        def rodar(self):
            self.root.mainloop()

    try:
        registrar(f"INICIO versao {VERSAO}")
        App().rodar()
    except Exception:
        import traceback
        registrar("ERRO FATAL\n" + traceback.format_exc())
        raise


if __name__ == "__main__":
    main()


























