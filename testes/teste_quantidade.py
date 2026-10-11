import sys; import os as _o; sys.path.insert(0,_o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
import contador_almas as ca, tempfile, os
def novo():
    p = ca.Progresso(os.path.join(tempfile.mkdtemp(),"p.json"))
    p.somar("Kalgolem de Drakana",1,salvar=False)
    return ca.Rastreador(ca.PADROES_PADRAO, p.canonico, p.completar)
L="Obteve Pontos da Alma Kalgolem de Drakana "
K="Voce obteve 1.2{} Kinah."
base=[(K.format(i),100+i*20,14) for i in range(5)]
def roda(nome, quadros, esperado):
    r=novo(); tot=[]
    for f in quadros: tot += r.quadro_pos(f)
    print("OK" if len(tot)==esperado else "XX", nome, len(tot), "esperado", esperado)
roda("x2 simples", [base, base[1:]+[(L+"x2.",200,14)], base[1:]+[(L+"x2.",200,14)]], 2)
roda("x1 depois vira x2", [base, base[1:]+[(L+"x1.",200,14)], base[1:]+[(L+"x2.",200,14)], base[1:]+[(L+"x2.",200,14)]], 2)
roda("x2 e x1 alternando", [base, base[1:]+[(L+"x2.",200,14)], base[1:]+[(L+"x1.",200,14)], base[1:]+[(L+"x2.",200,14)]], 2)
roda("x7 = 1", [base, base[1:]+[(L+"x7.",200,14)], base[1:]+[(L+"x1.",200,14)]], 1)
roda("x2 antigo no chat", [base[:4]+[(L+"x2.",180,14)], base[:4]+[(L+"x2.",180,14)]], 0)
roda("x2 rolando pra cima", [base, base[1:]+[(L+"x2.",200,14)], base[2:]+[(L+"x2.",180,14),(K.format(9),200,14)]], 2)
sob=lambda f,k: [(t,y-20*k,h) for t,y,h in f]
roda("x2 + x1 seguidos (certo)", [base, sob(base[2:],2)+[(L+"x2.",180,14),(L+"x1.",200,14)], sob(base[2:],2)+[(L+"x2.",180,14),(L+"x1.",200,14)]], 3)
roda("x2 + x2 iguais seguidos", [base, sob(base[2:],2)+[(L+"x2.",180,14),(L+"x2.",200,14)], sob(base[3:],3)+[(L+"x2.",160,14),(L+"x2.",180,14),(K.format(8),200,14)]], 4)
