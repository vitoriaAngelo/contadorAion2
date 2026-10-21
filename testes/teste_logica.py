"""Mensagens reais do Aion 2: so os pontos de alma do jogador contam."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from contador_almas import Extrator, PADROES_PADRAO


class MensagensDoJogo(unittest.TestCase):
    def setUp(self):
        self.extrator = Extrator(PADROES_PADRAO)

    def test_pontos_de_alma(self):
        self.assertEqual(self.extrator.nome_em(
            'Obteve Pontos da Alma Zaif Mutante x1.'), 'Zaif Mutante')
        self.assertEqual(self.extrator.nome_em(
            'Obteve Pontos da Alma Guerreiro Mumu x1.'), 'Guerreiro Mumu')

    def test_nao_soma_alma_do_grupo(self):
        self.assertIsNone(self.extrator.nome_em(
            'Fulano obteve Alma: Guerreiro Mumu (Vinculado) através da distribuição do grupo.'))
        self.assertIsNone(self.extrator.nome_em('Você obteve 463 Kinah.'))


if __name__ == '__main__':
    unittest.main()
