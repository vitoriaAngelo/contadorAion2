import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca


class Regressoes(unittest.TestCase):
    def test_area_automatica_guarda_linhas_de_rajada(self):
        mon = {'left': 0, 'top': 0, 'width': 1920, 'height': 1080}
        pequena = {'left': 30, 'top': 808, 'width': 407, 'height': 218}
        maior = ca.ampliar_regiao_chat(pequena, [mon])
        self.assertEqual(maior['top'] + maior['height'], 1026)
        self.assertEqual(maior['height'], 600)
        self.assertEqual(ca.ampliar_regiao_chat(maior, [mon]), maior)

    def test_uma_linha_alterada_dispara_leitura(self):
        from PIL import Image, ImageDraw
        cfg = dict(ca.CONFIG_PADRAO)
        leitor = ca.Leitor(cfg)
        antes = Image.new('L', (96, 100), 'black')
        depois = antes.copy()
        ImageDraw.Draw(depois).rectangle((10, 80, 85, 82), fill='white')
        leitor._mini = antes
        self.assertFalse(leitor._igual(depois))
        self.assertTrue(leitor._igual(antes))

    def test_nomes_ambiguos_e_palavras_extras(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            p = ca.Progresso(os.path.join(pasta, 'p.json'))
            p.monstros = [dict(nome=n, apelidos=[]) for n in
                          ['Lobo de Gelo', 'Lobo de Galo', 'Zaif Mutante']]
            self.assertIsNone(p.achar('Lobo de Gulo'))
            self.assertIsNone(p.achar('Zeit Mutante'))
            self.assertIsNone(p.achar('Lobo Maior de Gelo'))
            self.assertEqual(p.achar('Lobo de Gelo')['nome'], 'Lobo de Gelo')
            self.assertEqual(p.achar('Zaif Mutanle')['nome'], 'Zaif Mutante')

    def test_rajada_substitui_chat_sem_recontar(self):
        r = ca.Rastreador(ca.PADROES_PADRAO)
        def quadro(linhas):
            return [(t, 100 + i * 38, 22) for i, t in enumerate(linhas)]
        antigo = quadro(['Voce obteve 123 Kinah.',
                         'Voce obteve 456 Kinah.',
                         'Obteve Pontos da Alma Guerreiro Mumu x1.',
                         'Obteve Pontos da Alma Guerreiro Mumu x1.'])
        novo = quadro(['Voce obteve 789 Kinah.', 'Voce obteve 987 Kinah.',
                      'Obteve Pontos da Alma Papis x1.',
                      'Obteve Pontos da Alma Papis x1.'])
        self.assertEqual(r.quadro_pos(antigo, 0), [])
        self.assertEqual(r.quadro_pos(novo, 1), [])
        self.assertEqual(r.quadro_pos(novo, 2), ['Papis', 'Papis'])
        self.assertEqual(r.quadro_pos(novo, 3), [])
        self.assertEqual(r.quadro_pos(antigo, 4), [])
        self.assertEqual(r.quadro_pos(antigo, 5), [])

    def test_chat_lotado_com_novas_referencias(self):
        r = ca.Rastreador(ca.PADROES_PADRAO)
        def quadro(inicio):
            linhas = []
            for i in range(inicio, inicio + 5):
                nome = 'Papis' if i % 2 else 'Zaif Mutante'
                linhas.extend((f'Voce obteve {100 + i * 7} Kinah.',
                               f'Obteve Pontos da Alma {nome} x1.'))
            return [(linha, 100 + j * 38, 22)
                    for j, linha in enumerate(linhas)]
        self.assertEqual(r.quadro_pos(quadro(0), 0), [])
        esperado = 0
        for rodada in range(1, 5):
            atual = quadro(rodada * 5)
            self.assertEqual(r.quadro_pos(atual, rodada * 2), [])
            novas = r.quadro_pos(atual, rodada * 2 + 1)
            esperado += 5
            self.assertEqual(len(novas), 5)
            self.assertEqual(len(r.quadro_pos(atual, rodada * 2 + 1.5)), 0)
        self.assertEqual(esperado, 20)


if __name__ == '__main__':
    unittest.main()
