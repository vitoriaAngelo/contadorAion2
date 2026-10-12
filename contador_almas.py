# -*- coding: utf-8 -*-
"""
Contador de Almas - overlay para Aion 2.

Captura uma pequena area da tela (o chat / log do sistema), le o texto com OCR
e, quando aparece a mensagem de alma de um monstro, soma +1 no contador dele.

NAO le a memoria do jogo, NAO intercepta a rede e NAO pede login.
Funciona como o OBS: apenas olha os pixels da tela.
"""
import asyncio
import ctypes
import difflib
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
VERSAO = "1.6.7"
REPO_RAW = "https://raw.githubusercontent.com/vitoriaAngelo/contadorAion2/main/"
# arquivos que a atualizacao troca (os que nao existirem na pasta sao ignorados,
# menos o programa, que e obrigatorio)
ARQUIVOS_ATUALIZAR = ["contador_almas.py", "icone.ico", "desinstalar.ps1", "requirements.txt"]


def versao_tupla(v):
    n = [int(x) for x in re.findall(r"\d+", str(v))[:3]]
    return tuple(n + [0] * (3 - len(n)))


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
        if os.path.exists(os.path.join(pasta, nome)):
            try:
                extras[nome] = baixar_texto(REPO_RAW + nome)
            except Exception:
                pass
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
    return extras.get("requirements.txt", req_antes) != req_antes


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
        {"nome": "Invas\u00e3o", "minuto": 30, "horas": None, "duracao": 3},
        {"nome": "Fenda", "minuto": 0,
         "horas": [2, 5, 8, 11, 14, 17, 20, 23], "duracao": 10},
        {"nome": "Kairah", "minuto": 0,
         "horas": [1, 4, 7, 10, 13, 16, 19, 22], "duracao": 30},
        {"nome": "Nahma", "minuto": 0, "horas": [21], "dias": [4, 6], "duracao": 30},
    ],      # almas para cada estrela (somam a meta: 5+25+75=105)
    "regiao": None,            # {"left","top","width","height"} em pixels reais
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
    volume de 0.1 a 1.0 (0.5 = o som original)."""
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
        vol = max(0.1, min(1.0, float(vol)))
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
            if melhor is None or (andando and not melhor[2]) or \
                    (andando == melhor[2] and t < melhor[0]):
                melhor = [t, [nome], andando, fim]
            elif t == melhor[0] and andando == melhor[2] and nome not in melhor[1]:
                melhor[1].append(nome)
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
    cfg.update(salvo)
    cfg["versao_cfg"] = 9
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
        for r in self.regs:
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
        if e > max(1, len(max(x, y, key=len)) // 4) and not (len(x) <= 4 and e <= 2 and x[0] == y[0]):
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
                            "qtd": quantidade_alma(t) if c[1] else 1, "cont": 0,
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
        if sc < -3 and len(novos) >= 3 or (not pares and len(cur) >= 4):
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
                        if m["nome"] and not velha(i):
                            m["cont"] = m["qtd"]
                            out += [m["nome"]] * m["qtd"]
                    return out
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

    def salvar(self):
        try:
            salvar_json(self.arquivo, {"monstros": self.monstros})
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
        # junta sozinho erros de OCR (ex.: "Zeit Mutante" -> "Zaif Mutante"),
        # mas monstros parecidos com uma palavra diferente ("Espirito da Agua
        # Superior" x "Espirito da Terra Superior") ficam separados
        melhor, nota = None, 0.0
        for m in self.monstros:
            for c in self._chaves(m):
                if mesmo_monstro(n, c):
                    r = parecido(k, c.replace(" ", ""))
                    if r > nota:
                        melhor, nota = m, r
        if melhor is None:
            # lixo do cenario no MEIO do nome (linha quebrada):
            # 'espirito do fogo eg intermediario' -> 'espirito do fogo intermediario'
            pn = n.split()
            for m in self.monstros:
                for c in self._chaves(m):
                    pc = c.split()
                    if len(pn) - len(pc) not in (1, 2) or len(pc) < 2:
                        continue
                    if not mesmo_monstro(pn[-1], pc[-1]) or not mesmo_monstro(pn[0], pc[0]):
                        continue
                    i = j = 0
                    while i < len(pn) and j < len(pc):
                        if mesmo_monstro(pn[i], pc[j]):
                            j += 1
                        i += 1
                    if j == len(pc):
                        return m
        return melhor

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
        if salvar:
            self.salvar()
        return m

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
        self.monstros.remove(m)
        self.salvar()

    def zerar_tudo(self):
        for m in self.monstros:
            m["qtd"] = 0
        self.salvar()


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
            VOLUME_AVISO["v"] = float(self.cfg.get("volume_aviso", 0.5))
            self.prog = Progresso(ARQ_DADOS)
            self.rastreador = Rastreador(self.cfg["padroes"], self.prog.canonico, self.prog.completar,
                                         self.prog.comeco_de_outro)
            self.destaque = {}
            self.motor_nome = "iniciando OCR..."
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
                "evento": ("Segoe UI", 8, "bold"),
            }
            self.fontes = {k: tkfont.Font(root=self.root, family=f, size=t, weight=p)
                           for k, (f, t, p) in self._fontes_base.items()}
            self._aplicar_tamanho_fontes()
            self.f_titulo = self.fontes["titulo"]
            self.f_nome = self.fontes["nome"]
            self.f_peq = self.fontes["peq"]

            self._montar_cabecalho()
            self.corpo = tk.Frame(self.root, bg=COR["fundo"])
            self.corpo.pack(fill="both", expand=True, padx=self.px(8), pady=(0, self.px(8)))
            self.render()

            self.leitor = Leitor(self.cfg)
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
            self.root.after(300, self.atualizar_areas_proprias)

        def detectar_chat(self):
            self.status_extra = "procurando o chat…"
            self.atualizar_status()
            self.leitor.pedir_deteccao.set()
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

        def botao(self, pai, texto, cmd, cor=None, fonte=None):
            b = tk.Label(pai, text=texto, bg=pai["bg"], fg=cor or COR["suave"],
                         font=fonte or self.fontes["botao"], cursor="hand2",
                         padx=self.px(4))
            b.bind("<Button-1>", lambda e: cmd(e))
            b.bind("<Enter>", lambda e: b.configure(fg=COR["texto"]))
            b.bind("<Leave>", lambda e: b.configure(fg=cor or COR["suave"]))
            return b

        # ---------------- cabecalho ----------------
        def _montar_cabecalho(self):
            h = tk.Frame(self.root, bg=COR["fundo"])
            h.pack(fill="x", padx=self.px(8), pady=(self.px(6), self.px(4)))
            self.cab = h
            t = tk.Label(h, text="\u2726 Almas", fg=COR["alma"], bg=COR["fundo"],
                         font=self.f_titulo)
            t.pack(side="left")
            self.lbl_status = tk.Label(h, text="", bg=COR["fundo"], font=self.f_peq)
            self.lbl_status.pack(side="left", padx=(self.px(8), 0))
            # aparece so quando tem versao nova no GitHub
            self.lbl_atualizar = tk.Label(h, text="", fg=COR["ouro"], bg=COR["fundo"],
                                          font=self.fontes["fechar"], cursor="hand2")
            self.lbl_atualizar.pack(side="left", padx=(self.px(6), 0))
            self.lbl_atualizar.bind("<Button-1>", lambda e: self._clique_atualizar())

            self.botao(h, "\u2715", lambda e: self.sair()).pack(side="right")
            self.botao(h, "\u2013", lambda e: self.minimizar()).pack(side="right")
            # alterna modo normal / compacto
            self.b_modo = self.botao(h, "\u25a4" if self.cfg.get("compacto") else "\u2261",
                                     lambda e: self.alternar_compacto())
            self.b_modo.pack(side="right")
            self.b_recolher = self.botao(h, "\u25be", lambda e: self.alternar_recolher())
            self.b_recolher.pack(side="right")
            self.botao(h, "\u2699", self.menu_principal).pack(side="right")
            self.b_pausa = self.botao(h, "\u23f8", lambda e: self.alternar_pausa())
            self.b_pausa.pack(side="right")

            for w in (h, t, self.lbl_status):
                w.bind("<ButtonPress-1>", self._arrastar_ini)
                w.bind("<B1-Motion>", self._arrastar)
                w.bind("<ButtonRelease-1>", self._arrastar_fim)
            self.atualizar_status()

        def _arrastar_ini(self, e):
            self._dx = e.x_root - self.root.winfo_x()
            self._dy = e.y_root - self.root.winfo_y()

        def _arrastar(self, e):
            self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

        def _arrastar_fim(self, e):
            self.cfg["posicao"] = [self.root.winfo_x(), self.root.winfo_y()]
            self.salvar_cfg()

        def atualizar_status(self):
            if self.erro:
                txt, cor = "\u25cf erro no OCR", COR["erro"]
            elif getattr(self, "status_extra", None):
                txt, cor = "\u25cf " + self.status_extra, COR["ouro"]
            elif not self.cfg.get("regiao"):
                txt, cor = "\u25cf procurando o chat\u2026", COR["ouro"]
            elif not self.leitor_ativo():
                txt, cor = "\u25cf pausado", COR["suave"]
            else:
                txt, cor = "\u25cf lendo", COR["alma"]
            self.lbl_status.configure(text=txt, fg=cor)
            if hasattr(self, "b_pausa"):
                self.b_pausa.configure(text="\u25b6" if not self.leitor_ativo() else "\u23f8")

        def leitor_ativo(self):
            return hasattr(self, "leitor") and self.leitor.ativo.is_set()

        def alternar_pausa(self):
            if self.leitor.ativo.is_set():
                self.leitor.ativo.clear()
            else:
                self.rastreador.reiniciar()  # nao conta o que apareceu enquanto pausado
                self.leitor.ativo.set()
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
            concl = [m for m in todos if m["qtd"] >= meta]
            # ordem fixa (ordem em que os monstros apareceram): nao pula de lugar
            pend = [m for m in todos if m["qtd"] < meta]
            lista = pend + (concl if self.cfg.get("mostrar_concluidos") else [])
            if self.cfg.get("recolhido"):
                lista = lista[:1]
            # todos ficam na lista; acima de 6 aparece a rolagem
            visiveis, ocultos = lista, 0

            rodape = ""
            mostra_rodape = not self.cfg.get("recolhido")
            if todos and mostra_rodape:
                rodape = f"{len(concl)}/{len(todos)} conclu\u00eddos"
                if ocultos:
                    rodape += f"  \u00b7  +{ocultos} ocultos"
            vazio = ""
            if not todos:
                vazio = ("Procurando o chat do jogo\u2026 deixe o jogo aberto com o chat "
                         "vis\u00edvel (linhas \u201cVoc\u00ea obteve\u2026\u201d)."
                         if not self.cfg.get("regiao") else
                         "Chat encontrado. Aguardando a primeira alma\u2026")

            estrutura = (tuple(id(m) for m in visiveis), mostra_rodape, vazio,
                         bool(self.cfg.get("recolhido")))
            if estrutura == getattr(self, "_estrutura", None):
                # so atualiza os numeros (sem apagar nada)
                for m in visiveis:
                    w = self._ui.get(id(m))
                    if w:
                        self._atualizar_linha(m, meta, w)
                if getattr(self, "_lbl_rodape", None) and self._lbl_rodape.cget("text") != rodape:
                    self._lbl_rodape.configure(text=rodape)
                return

            if getattr(self, "editando", None) is not None:
                # usuario digitando na caixinha: monta de novo depois
                self.root.after(500, self.render_em_breve)
                return
            self._estrutura = estrutura
            for w in self.corpo.winfo_children():
                w.destroy()
            self._ui = {}
            self._lbl_rodape = None
            self.b_recolher.configure(text="\u25b8" if self.cfg.get("recolhido") else "\u25be")
            if vazio:
                tk.Label(self.corpo, text=vazio, fg=COR["suave"], bg=COR["fundo"],
                         font=self.f_peq, wraplength=self.px(240),
                         justify="left").pack(anchor="w", pady=self.px(4))
            self._rolagem = None
            limite = max(1, int(self.cfg.get("max_linhas", 6)))
            if len(visiveis) > limite:
                pai = self._montar_rolagem()
                for m in visiveis:
                    self._ui[id(m)] = self._linha(m, meta, pai)
                self._ajustar_rolagem(visiveis[:limite])
            else:
                for m in visiveis:
                    self._ui[id(m)] = self._linha(m, meta)
            self._lbl_evento = None
            if mostra_rodape:
                rod = tk.Frame(self.corpo, bg=COR["fundo"])
                rod.pack(fill="x", pady=(self.px(4), 0))
                self._lbl_rodape = tk.Label(rod, text=rodape, fg=COR["suave"],
                                            bg=COR["fundo"], font=self.f_peq)
                self._lbl_rodape.pack(side="left")
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

        def atualizar_evento(self, agendar=True):
            """Relogio do proximo evento: mm:ss, a cada meio segundo. No ultimo
            minuto fica vermelho e piscando. So mexe nesses dois textos."""
            import datetime as dt
            lbl, rel = getattr(self, "_lbl_evento", None), getattr(self, "_lbl_relogio", None)
            if lbl is not None and rel is not None:
                try:
                    agora = dt.datetime.now()
                    info = getattr(self, "_ev_cache", None)
                    # so recalcula a agenda quando o evento comeca/termina
                    if info is None or agora >= (info[2] if info[3] else info[1]):
                        info = evento_info(self.cfg.get("eventos", []), agora)
                        self._ev_cache = info
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

        def mudar_volume(self, v):
            self.cfg["volume_aviso"] = v
            VOLUME_AVISO["v"] = v
            self.salvar_cfg()
            tocar_aviso()  # ja toca para voce ouvir como ficou

        def alternar_som(self):
            self.cfg["aviso_som"] = not self.cfg.get("aviso_som", True)
            self.salvar_cfg()
            if self.cfg["aviso_som"]:
                tocar_aviso()

        def _atualizar_linha(self, m, meta, w):
            feitas, falta_tier, n_tiers = estrelas(m["qtd"], self.cfg.get("tiers", [5, 25, 75]))
            completo = feitas >= n_tiers
            # contador e barra mostram a estrela atual (ex.: 3a estrela 70/75)
            _tiers = self.cfg.get("tiers", [5, 25, 75])
            est_t, faltam_t = tier_atual(m["qtd"], _tiers)
            tamanho_t = _tiers[est_t - 1]
            cor_barra = COR["ouro"] if completo else COR["alma"]
            novo = {
                "cont": f"{tamanho_t - faltam_t}/{tamanho_t}",
                "falta": "",
            }
            # estrelas: douradas = completas; contorno dourado = a que esta
            # fazendo agora; cinza = ainda nao chegou
            for i, st in enumerate(w["estrelas"], 1):
                if i <= feitas:
                    txt, cor = "\u2605", COR["ouro"]
                elif i == feitas + 1:
                    txt, cor = "\u2606", COR["ouro"]
                else:
                    txt, cor = "\u2606", "#5a6070"
                if st.cget("text") != txt or st.cget("fg") != cor:
                    st.configure(text=txt, fg=cor)
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
                return  # modo compacto: sem barra nem caixinha
            W, H = w["W"], w["H"]
            frac = (tamanho_t - faltam_t) / tamanho_t if tamanho_t else 0
            w["cv"].coords(w["barra"], 0, 0, int(W * frac), H)
            w["cv"].itemconfigure(w["barra"], fill=cor_barra)
            cx = w["caixa"]
            tenho_tier = tamanho_t - faltam_t
            if getattr(self, "editando", None) is not cx and cx.get() != str(tenho_tier):
                cx.delete(0, "end")
                cx.insert(0, str(tenho_tier))

        def _estrelas_na(self, topo, m, w, bg):
            tiers = self.cfg.get("tiers", [5, 25, 75])
            w["estrelas"] = []
            for i in range(1, len(tiers) + 1):
                st = tk.Label(topo, text="\u2606", fg="#5a6070", bg=bg,
                              font=self.fontes["estrela"], cursor="hand2", padx=0)
                st.pack(side="left", padx=(self.px(4) if i == 1 else 0, 0))
                st.bind("<Button-1>", lambda e, m=m, i=i: self.escolher_estrela(m, i))
                w["estrelas"].append(st)

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
            topo.pack(fill="x", padx=self.px(6), pady=self.px(2))
            w = {"frame": f}
            self._caixa_nome(topo, w, bg)
            self._estrelas_na(topo, m, w, bg)
            self.botao(topo, "\u2715", lambda e, m=m: self.fechar_monstro(m),
                       cor=COR["erro"], fonte=self.fontes["fechar"]).pack(side="right")
            self.botao(topo, "+", lambda e, m=m: self.ajustar(m, +1),
                       fonte=self.fontes["botao_b"]).pack(side="right")
            self.botao(topo, "\u2212", lambda e, m=m: self.ajustar(m, -1),
                       fonte=self.fontes["botao_b"]).pack(side="right")
            w["cont"] = tk.Label(topo, text="", fg=COR["alma"], bg=bg, font=self.f_nome,
                                 width=6, anchor="e", cursor="hand2")
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
                       cor=COR["erro"], fonte=self.fontes["fechar"]).pack(side="right")
            self.botao(topo, "+", lambda e, m=m: self.ajustar(m, +1),
                       fonte=self.fontes["botao_b"]).pack(side="right")
            self.botao(topo, "\u2212", lambda e, m=m: self.ajustar(m, -1),
                       fonte=self.fontes["botao_b"]).pack(side="right")
            w["cont"] = tk.Label(topo, text="", fg=COR["alma"], bg=bg, font=self.f_nome,
                                 width=7, anchor="e")
            w["cont"].pack(side="right", padx=(0, self.px(4)))

            base = tk.Frame(f, bg=bg)
            base.pack(fill="x", padx=self.px(8), pady=(self.px(3), self.px(6)))
            W, H = self.px(int(130 * self._escala_letra())), self.px(6)
            w["W"], w["H"] = W, H
            w["cv"] = tk.Canvas(base, width=W, height=H, bg=COR["barra"], highlightthickness=0)
            w["cv"].pack(side="left")
            w["barra"] = w["cv"].create_rectangle(0, 0, 0, H, fill=COR["alma"], width=0)
            # texto "faltam X p/ N estrela" removido a pedido (fica so a caixinha)
            w["falta"] = tk.Label(base, text="", fg=COR["suave"], bg=bg, font=self.f_peq)
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

        def mudar_tamanho_letra(self, nome):
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
                self.render()

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
            menu.add_command(label="Detectar o chat automaticamente", command=self.detectar_chat)
            menu.add_command(label="Selecionar \u00e1rea do chat na m\u00e3o\u2026", command=self.selecionar_regiao)
            menu.add_command(label="Ver leitura / ajustar padr\u00f5es\u2026", command=self.abrir_debug)
            menu.add_separator()
            menu.add_command(label="Adicionar monstro\u2026", command=self.adicionar)
            menu.add_command(label="Almas por estrela: " + " / ".join(str(t) for t in self.cfg["tiers"]) + "\u2026",
                             command=self.mudar_meta)
            menu.add_checkbutton(label="Mostrar conclu\u00eddos",
                                 onvalue=True, offvalue=False,
                                 variable=tk.BooleanVar(value=self.cfg.get("mostrar_concluidos")),
                                 command=self.alternar_concluidos)
            sub = tk.Menu(menu, tearoff=0)
            for v in (1.0, 0.9, 0.8, 0.7, 0.6):
                sub.add_command(label=f"{int(v*100)}%", command=lambda v=v: self.opacidade(v))
            menu.add_cascade(label="Opacidade", menu=sub)
            sub_letra = tk.Menu(menu, tearoff=0)
            atual = self.cfg.get("tamanho_letra", "normal")
            for nome in ("pequena", "normal", "grande", "muito grande"):
                sub_letra.add_command(label=("\u2713 " if nome == atual else "    ") + nome.capitalize(),
                                      command=lambda n=nome: self.mudar_tamanho_letra(n))
            menu.add_cascade(label="Tamanho da letra", menu=sub_letra)
            menu.add_checkbutton(label="Modo compacto (uma linha por bicho)",
                                 onvalue=True, offvalue=False,
                                 variable=tk.BooleanVar(value=bool(self.cfg.get("compacto"))),
                                 command=self.alternar_compacto)
            menu.add_checkbutton(label="Som 1 minuto antes dos eventos",
                                 onvalue=True, offvalue=False,
                                 variable=tk.BooleanVar(value=self.cfg.get("aviso_som", True)),
                                 command=self.alternar_som)
            sub_vol = tk.Menu(menu, tearoff=0)
            vol = float(self.cfg.get("volume_aviso", 0.5))
            for nome, v in (("Baixo", 0.25), ("M\u00e9dio", 0.5), ("Alto", 0.75), ("M\u00e1ximo", 1.0)):
                sub_vol.add_command(label=("\u2713 " if abs(v - vol) < 0.01 else "    ") + nome,
                                    command=lambda v=v: self.mudar_volume(v))
            menu.add_cascade(label="Volume do aviso", menu=sub_vol)
            menu.add_command(label="Testar o som do aviso", command=lambda: tocar_aviso())
            menu.add_checkbutton(label="Aparecer na stream (OBS/Discord)",
                                 onvalue=True, offvalue=False,
                                 variable=tk.BooleanVar(value=not self.cfg.get("esconder_da_captura", False)),
                                 command=self.alternar_stream)
            menu.add_separator()
            menu.add_command(label="Zerar todos os contadores\u2026", command=self.zerar)
            menu.add_command(label="Abrir pasta dos dados", command=self.abrir_pasta)
            menu.add_command(label="Gerar relat\u00f3rio para suporte", command=self.gerar_relatorio)
            menu.add_command(label=f"Procurar atualiza\u00e7\u00e3o (vers\u00e3o {VERSAO})\u2026",
                             command=self.procurar_atualizacao)
            menu.add_separator()
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
                    subprocess.run([exe, "-m", "pip", "install", "--user", "-q",
                                    "--disable-pip-version-check", "-r",
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
                if self.leitor.ultima:
                    self.leitor.ultima[0].save(os.path.join(BASE, "captura_original.png"))
                    self.leitor.ultima[1].save(os.path.join(BASE, "captura_tratada.png"))
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
                    for nome in ("diagnostico.log", "diagnostico.log.old", "config.json",
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
            if not self.leitor.ultima:
                self.dialogo(messagebox.showinfo, APP_NOME, "Nada capturado ainda.")
                return
            bruta, tratada = self.leitor.ultima
            bruta.save(os.path.join(BASE, "captura_original.png"))
            tratada.save(os.path.join(BASE, "captura_tratada.png"))
            self.abrir_pasta()

        def restaurar_padroes(self):
            self.txt_padroes.delete("1.0", "end")
            self.txt_padroes.insert("1.0", "\n".join(PADROES_PADRAO))
            self.salvar_padroes()

        def atualizar_previa(self):
            par = self.leitor.ultima if hasattr(self, "leitor") else None
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
            if self.erro:
                info += f"\nERRO: {self.erro}"
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
            try:
                while True:
                    tipo, dado = self.leitor.fila.get_nowait()
                    if tipo == "linhas":
                        self.ao_ler(dado[0], None, dado[1])
                    elif tipo == "linhas_pos":
                        self.ao_ler([t for t, _, _ in dado[0]], dado[0], dado[1])
                    elif tipo == "repetido":
                        pass
                    elif tipo == "motor":
                        registrar("MOTOR: " + str(dado))
                        self.motor_nome = dado
                        self.atualizar_status()
                    elif tipo == "erro":
                        registrar("ERRO OCR: " + str(dado))
                        self.erro = dado
                        self.atualizar_status()
                        self.dialogo(messagebox.showerror, APP_NOME, dado +
                                     "\n\nVerifique se o Tesseract est\u00e1 instalado (veja o README, se\u00e7\u00e3o Problemas).")
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
            self.root.after(100, self.consumir)

        def ao_ler(self, linhas, itens=None, t0=None):
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
            for n in nomes:
                m = self.prog.somar(n, 1, salvar=False)
                self.destaque[normalizar(m["nome"])] = time.time()
                self._rolar_para = m
            if nomes:
                self.prog.salvar()  # grava uma vez so, mesmo com varias almas
            # diagnostico: captura a cada 10s e registro do que leu/contou
            agora = time.time()
            if agora - getattr(self, "_ultimo_save", 0) > 10 and self.leitor.ultima:
                self._ultimo_save = agora
                try:
                    self.leitor.ultima[0].save(os.path.join(BASE, "captura_original.png"))
                    self.leitor.ultima[1].save(os.path.join(BASE, "captura_tratada.png"))
                except Exception:
                    pass
            almas = [l for l in linhas if self.rastreador.extrator.nome_em(l)]
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
