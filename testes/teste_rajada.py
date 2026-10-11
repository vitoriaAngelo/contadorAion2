"""Simula o chat do Aion 2 e confere a contagem por posicao.

Comportamento do chat (visto no diagnostico do PC):
  * ancorado embaixo; mostra no maximo CAP linhas; as antigas saem pelo topo;
  * a janela inteira some depois de FADE s sem mensagem nova e volta com o
    historico quando chega mensagem."""
import random, sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca

PASSO, BASE, ALT = 38.0, 1150.0, 22
ALMA = "Obteve Pontos da Alma {} x1."


def simular(eventos, duracao, dt=1.0, fade=15.0, cap=8, ruido=0.0, seed=1,
            cobrir=(), jogador=False, abrir=(), borrado=0.0):
    rnd = random.Random(seed)
    chat, ultima_msg = [], -999
    p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
    r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico)
    real = {}
    ev = sorted(eventos, key=lambda e: e[0])
    t = 0.0
    while t <= duracao:
        while ev and ev[0][0] <= t:
            te, msgs = ev.pop(0)
            for m in msgs:
                chat.append(m)
                ultima_msg = te
                if "Alma" in m:
                    nome = m.split("Alma ")[1].replace(" x1.", "")
                    real[nome] = real.get(nome, 0) + 1
        itens = []
        aberto = any(a <= t < b for a, b in abrir)
        base = BASE - (1.3 * PASSO if aberto else 0)   # caixa de digitar empurra
        cap_v = cap + (8 if aberto else 0)              # e mostra mais historico
        if t - ultima_msg < fade or aberto:
            vis = chat[-cap_v:]
            muito_borrado = borrado and rnd.random() < borrado
            for i, m in enumerate(reversed(vis)):
                y = base - i * PASSO + rnd.uniform(-3, 3)
                if muito_borrado and rnd.random() < 0.6:
                    m = m[:12] + "".join(rnd.choice("aeiouxyz ~") for _ in range(10))
                texto = m
                if ruido and rnd.random() < ruido:
                    texto = texto.replace("Pontos da", "Pontos daA").replace("e", "ê", 1) + " Ns ~"
                if ruido and rnd.random() < ruido / 3:
                    continue  # OCR perdeu a linha neste quadro
                itens.append((texto, y, ALT))
            # lixo da grama que o OCR le entre as linhas do chat
            for i in range(len(vis)):
                if rnd.random() < 0.5:
                    lixo = "".join(rnd.choice("ae SNRBPgTo~-") for _ in range(rnd.randint(6, 25)))
                    itens.append((lixo, base - i * PASSO - PASSO / 2 + rnd.uniform(-4, 4), rnd.randint(12, 35)))
            # lixo do cenario acima do chat (nomes de lugares)
            itens.append(("Leste das Colinas de Gribade", BASE - (cap + 3) * PASSO, ALT))
        if any(a <= t < b for a, b in cobrir):
            itens = [("Inventario", 300, 22), ("Monstros", 340, 22), ("Sair do jogo", 380, 22)]
        for n in r.quadro_pos(sorted(itens, key=lambda x: x[1]), t):
            p.somar(n)
        t = round(t + dt, 3)
    final = {m["nome"]: m["qtd"] for m in p.monstros if m["qtd"]}
    return real, final


def kinah(rnd=random.Random(7)):
    return f"Voce obteve {rnd.randint(50, 900)} Kinah."


def kill(nome):
    return [kinah(), ALMA.format(nome)]


casos = {}
Z, P = "Zaif Mutante", "Papis"
# o programa ja esta aberto antes da luta: 1o quadro com chat antigo
inicio = [(0.0, [kinah(), "[Zira] alguem pt?", kinah()])]

casos["1 por vez (kill = kinah+alma)"] = simular(
    inicio + [(2 + i * 3.0, kill(Z)) for i in range(20)], 70)
casos["AoE: 6 abates de uma vez"] = simular(
    inicio + [(3.0, sum((kill(Z) for _ in range(6)), []))], 20, cap=14)
casos["AoE: 4 abates, varias vezes"] = simular(
    inicio + [(2 + i * 2.0, sum((kill(Z) for _ in range(4)), [])) for i in range(8)], 30, cap=14)
casos["dois monstros, leitura 0,5s"] = simular(
    inicio + [(1 + i * 0.8, kill(Z) + kill(P)) for i in range(15)], 30, dt=0.5, cap=10)
casos["so almas iguais (sem kinah)"] = simular(
    inicio + [(2 + i * 1.5, [ALMA.format(Z)] * 2) for i in range(8)], 25, cap=8)
casos["ruido de OCR 15%"] = simular(
    inicio + [(1 + i * 1.5, sum((kill(Z) for _ in range(3)), [])) for i in range(10)],
    30, ruido=0.15, seed=3, cap=14)
casos["chat some entre lutas"] = simular(
    inicio + [(2.0, kill(Z) * 1), (40.0, kill(Z) + kill(Z)), (80.0, kill(Z))], 100)
casos["menu ESC no meio"] = simular(
    inicio + [(1 + i * 2.0, kill(Z)) for i in range(10)], 25, cobrir=[(7.0, 10.0)])
casos["jogadores conversando junto"] = simular(
    inicio + [(1 + i * 2.0, kill(Z) + ["[Fulano] vamo dg?"]) for i in range(10)], 25)

casos["abre o chat varias vezes"] = simular(
    inicio + [(1 + i * 2.0, kill(Z)) for i in range(12)], 30,
    abrir=[(4.0, 7.0), (12.0, 13.0), (18.0, 24.0)])
casos["abre o chat com AoE"] = simular(
    inicio + [(1 + i * 3.0, sum((kill(Z) for _ in range(3)), [])) for i in range(8)], 30,
    abrir=[(5.0, 9.0), (15.0, 20.0)], cap=12)
casos["quadros muito borrados"] = simular(
    inicio + [(1 + i * 2.0, kill(Z) + kill(P)) for i in range(12)], 30, borrado=0.25, cap=12)
casos["um so monstro parado na tela"] = simular(
    [(2.0, [ALMA.format(P)])], 30)

for nome, (real, final) in casos.items():
    ok = "OK " if real == final else "XX "
    print(f"{ok}{nome:34} real={real}  contado={final}")
