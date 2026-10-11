import sys, os, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from contador_almas import Rastreador, Extrator, PADROES_PADRAO, Progresso, limpar_nome

ex = Extrator(PADROES_PADRAO)
casos = {
    "[Sistema] Você obteve: Alma de Lobo de Gelo x1": "Lobo de Gelo",
    "Alma do Krall Guerreiro foi adquirida.": "Krall Guerreiro",
    "You acquired [Frost Wolf Soul].": "Frost Wolf",
    "You acquired Frost Wolf Soul": "Frost Wolf",
    "Soul of Lepharist Scout obtained": "Lepharist Scout",
    "Você ganhou 1.234 de experiência": None,
    "alma de lob0 de gelo": "Lobo de Gelo", "Alma de Lobo de Gelo 2": "Lobo de Gelo",
}
for linha, esperado in casos.items():
    got = ex.nome_em(linha)
    print(("OK " if got == esperado else "XX "), repr(linha), "->", got)

XP = lambda n: f"Você ganhou {n} de experiência"
A = lambda m: f"[Sistema] Você obteve: Alma de {m} x1"

r = Rastreador(PADROES_PADRAO)
total = []
def q(linhas, t, rot):
    n = r.quadro(linhas, agora=t)
    total.extend(n)
    print(f"{rot:<40} -> {n}")

q([XP(100), A("Lobo de Gelo")], 0, "inicio (base, nao conta)")
q([XP(100), A("Lobo de Gelo")], 1, "igual")
q([XP(100), A("Lobo de Gelo"), XP(120), A("Lobo de Gelo")], 2, "+1 lobo (chat crescendo)")
q([XP(100), A("Lobo de Gelo"), XP(120), A("Lobo de Gelo")], 3, "igual")
q([A("Lobo de Gelo"), XP(120), A("Lobo de Gelo"), XP(95), A("Krall Guerreiro")], 4, "rolou: +1 krall")
q([A("Lobo de Geio"), XP(120), A("Lobo de Gelo"), XP(95), A("Krall Guerreiro")], 5, "erro de OCR, mesma tela")
q([], 6, "chat sumiu (fade)")
q([A("Lobo de Gelo"), XP(120), A("Lobo de Gelo"), XP(95), A("Krall Guerreiro")], 7, "chat voltou")
q(["Inventário", "Equipamento", "Monstros", "Sair do jogo"], 8, "menu ESC por cima")
q([A("Lobo de Gelo"), XP(120), A("Lobo de Gelo"), XP(95), A("Krall Guerreiro")], 9, "voltou do menu (nao recontar)")
q([XP(120), A("Lobo de Gelo"), XP(95), A("Krall Guerreiro"), XP(130), A("Lobo de Gelo")], 10, "rolou 2: +1 lobo")
print("TOTAL contado:", total)
assert total == ["Krall Guerreiro", "Lobo de Gelo"] or total == ["Lobo de Gelo", "Krall Guerreiro", "Lobo de Gelo"], total

d = tempfile.mkdtemp()
p = Progresso(os.path.join(d, "p.json"))
for n in total: p.somar(n)
p.somar("Krall Guerreira")
print([(m["nome"], m["qtd"]) for m in p.monstros])
a = p.achar("Krall Guerreira"); b = p.achar("Krall Guerreiro")
p.juntar(a, b)
p.somar("Krall Guerreira")
print("apos juntar:", [(m["nome"], m["qtd"], m["apelidos"]) for m in p.monstros])
