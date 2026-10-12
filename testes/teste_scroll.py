"""Sobe o chat bastante (scroll), fica um tempo la e volta: nao pode contar
de novo as almas que ja foram contadas."""
import os, random, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca

A = "Obteve Pontos da Alma "


def rodar(seed, falha=0.1, parado_em_cima=20, subir=15):
    rnd = random.Random(seed)
    p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
    r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar, p.comeco_de_outro)
    chat, real = [], 0
    kin = 100
    for _ in range(12):
        kin += rnd.randint(3, 400)
        chat.append(f"Você obteve {kin} Kinah.")
    cont = 0

    def quadro(fim):
        vis = chat[max(0, fim - 9):fim]
        return [(t, 100 + i * 20, 14) for i, t in enumerate(vis) if rnd.random() >= falha]

    def ver(fim, n=1):
        nonlocal cont
        for _ in range(n):
            for nome in r.quadro_pos(quadro(fim)):
                p.somar(nome, 1, salvar=False)
                cont += 1

    ver(len(chat), 3)
    for _ in range(30):  # farmando
        for _ in range(rnd.randint(1, 2)):
            real += 1
            chat.append(A + rnd.choice(["Supervisor Kuru", "Lupyllini Modificado"]) + " x1.")
            if rnd.random() < 0.8:
                kin += rnd.randint(3, 400)
                chat.append(f"Você obteve {kin} Kinah.")
        ver(len(chat), rnd.randint(2, 4))
    # sobe o chat aos poucos, fica parado, desce de volta
    fim = len(chat)
    for k in range(1, subir + 1):
        ver(fim - k, 1)
    ver(fim - subir, parado_em_cima)
    for k in range(subir - 1, -1, -1):
        ver(fim - k, 1)
    ver(fim, 5)
    # e continua farmando normal depois
    for _ in range(10):
        real += 1
        chat.append(A + "Supervisor Kuru x1.")
        kin += rnd.randint(3, 400)
        chat.append(f"Você obteve {kin} Kinah.")
        ver(len(chat), 3)
    return real, cont


for falha in (0.03, 0.10):
    mais = menos = 0
    for s in range(30):
        real, cont = rodar(s, falha=falha)
        mais += max(0, cont - real)
        menos += max(0, real - cont)
    print(f"OCR perdendo {falha:.0%} das linhas: a mais {mais}  a menos {menos}")
    if falha < 0.05:
        print("OK" if mais == 0 else "XX")
