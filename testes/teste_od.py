"""Contador de OD: le 'Voce obteve OD Fino x2.' e ignora o grupo."""
import os, sys, tempfile, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca
casos = {
    "Você obteve OD x1.": ("OD", 1), "Você obteve OD x2.": ("OD", 2), "Você obteve OD 2x": ("OD", 2),
    "Você obteve od 1x": ("OD", 1), "Você obteve OD Fino x2.": ("OD Fino", 2), "Você obteve OD Puro x1.": ("OD Puro", 1),
    "Você obteve OD Radiante x2.": ("OD Radiante", 2), "Vocé obteve 0D Fino x2.": ("OD Fino", 2),
    "Você obteve OD Radiahte x1.": ("OD Radiante", 1), "Você obteve OD Puro x7.": ("OD Puro", 1),
    "Hagi obteve OD Fino x2.": None, "Hagi obteve OD Fino (Vinculado)": None, "Você obteve Odium x1.": None,
    "Você obteve 463 Kinah.": None, "Obteve Pontos da Alma Kerubian x1.": None, "Você obteve Pedra de Aprimoramento x18.": None,
    "Você obteve ODrecido x1.": None,
}
ok = True
for l, esp in casos.items():
    r = ca.od_em(l)
    print("OK" if r == esp else "XX", repr(l), "->", r)
    ok &= r == esp
# rastreador: OD conta como item novo, junto com almas, sem duplicar
rnd = random.Random(2)
p = ca.Progresso(os.path.join(tempfile.mkdtemp(), "p.json"))
r = ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar, p.comeco_de_outro)
chat = [f"Você obteve {100 + i * 41} Kinah." for i in range(9)]
real_od, real_alma, kin = {t: 0 for t in ca.OD_TIPOS}, 0, 900
cont_od = {t: 0 for t in ca.OD_TIPOS}; cont_alma = 0
for _ in range(3):
    r.quadro_pos([(t, 100 + i * 20, 14) for i, t in enumerate(chat[-10:])])
for _ in range(40):
    chat.append("Obteve Pontos da Alma Kerubian x1."); real_alma += 1
    if rnd.random() < 0.5:
        t = rnd.choice(ca.OD_TIPOS); q = rnd.choice([1, 2]); real_od[t] += q
        chat.append(f"Você obteve {t} x{q}.")
    if rnd.random() < 0.3:
        chat.append("Hagi obteve OD Fino x2.")
    kin += rnd.randint(10, 300); chat.append(f"Você obteve {kin} Kinah.")
    for _ in range(3):
        for n in r.quadro_pos([(t, 100 + i * 20, 14) for i, t in enumerate(chat[-10:])]):
            if n.startswith("§OD:"): cont_od[n[4:]] += 1
            else: cont_alma += 1
print("OD real:", real_od, "contado:", cont_od, "| almas", real_alma, cont_alma)
ok &= cont_od == real_od and cont_alma == real_alma
print("OK" if ok else "XX")
