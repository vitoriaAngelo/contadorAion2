import json
import os
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contador_almas as ca
import pet_protocol as pp


def varint(n):
    out = bytearray()
    while n >= 128:
        out.append((n & 127) | 128)
        n >>= 7
    return bytes(out + bytes([n]))


def frame(op, payload):
    return varint(len(payload) + 6) + op + payload


class LeituraNpcap(unittest.TestCase):
    def test_snapshot_define_base_de_pontos_antes_dos_ganhos(self):
        eventos = []
        dec = pp.Decoder(eventos.append)
        snapshot = frame(pp.OP_ABYSS_SNAPSHOT,
                         b'\x01' + struct.pack('<IIQB', pp.ABYSS_POINT_ACHIEVEMENT,
                                               17000, 1_800_000_000_000, 1))
        ganho = frame(pp.OP_ABYSS_PROGRESS,
                      b'\x01' + struct.pack('<IIQB', pp.ABYSS_POINT_ACHIEVEMENT,
                                            17150, 1_800_000_001_000, 1))
        dec.segment('jogo', 1000, snapshot)
        dec.segment('jogo', 1000 + len(snapshot), ganho)
        self.assertEqual(eventos, [('ready',), ('abyss_points', 150)])

    def test_pontos_abismo_somam_diferenca_do_evento_de_conquista(self):
        eventos = []
        dec = pp.Decoder(eventos.append)

        def progresso(total):
            return b'\x01' + struct.pack('<IIQB', pp.ABYSS_POINT_ACHIEVEMENT,
                                         total, 1_800_000_000_000, 1)

        primeiro = frame(pp.OP_ABYSS_PROGRESS, progresso(17000))
        segundo = frame(pp.OP_ABYSS_PROGRESS, progresso(17980))
        repetido = frame(pp.OP_ABYSS_PROGRESS, progresso(17980))
        dec.segment('jogo', 1000, primeiro)
        dec.segment('jogo', 1000 + len(primeiro), segundo)
        dec.segment('jogo', 1000 + len(primeiro) + len(segundo), repetido)
        self.assertEqual(eventos, [('ready',), ('abyss_points', 980)])

    def test_kinah_soma_aumentos_de_inventario_sem_recontar_saldo(self):
        eventos = []
        dec = pp.Decoder(eventos.append)

        def alteracao(kind, instancia, quantidade):
            payload = bytearray(23)
            payload[0] = 1
            payload[1] = kind
            struct.pack_into('<I', payload, 3, instancia)
            struct.pack_into('<I', payload, 11, pp.KINAH_ITEM_ID)
            struct.pack_into('<Q', payload, 15, quantidade)
            return frame(pp.OP_INVENTORY_CHANGE, bytes(payload))

        primeiro = alteracao(0, 123, 363)
        segundo = alteracao(1, 123, 726)
        repetido = alteracao(1, 123, 726)
        dec.segment('jogo', 1000, primeiro)
        dec.segment('jogo', 1000 + len(primeiro), segundo)
        dec.segment('jogo', 1000 + len(primeiro) + len(segundo), repetido)
        self.assertEqual(eventos, [('ready',), ('kinah_gain', 363),
                                   ('kinah_gain', 363)])

    def test_od_coletado_atualiza_tipo_e_quantidade_pelo_inventario(self):
        eventos = []
        dec = pp.Decoder(eventos.append)

        def item(instancia, item_id, quantidade):
            payload = bytearray(23)
            payload[0] = 1
            payload[1] = 0
            struct.pack_into('<I', payload, 3, instancia)
            struct.pack_into('<I', payload, 11, item_id)
            struct.pack_into('<Q', payload, 15, quantidade)
            return frame(pp.OP_INVENTORY_CHANGE, bytes(payload))

        atual = 1000
        esperados = [('ready',)]
        for instancia, (item_id, tipo) in enumerate(pp.OD_ITEM_TYPES.items(), 10):
            dados = item(instancia, item_id, 2)
            dec.segment('jogo', atual, dados)
            atual += len(dados)
            esperados.append(('od_gain', tipo, 2))
        self.assertEqual(eventos, esperados)

    def test_assinatura_distingue_estado_de_outro_personagem(self):
        a = ca.assinatura_estado_pets({2: 1, 1: 3}, {1: 10})
        self.assertEqual(a, ca.assinatura_estado_pets({1: 3, 2: 1}, {1: 10}))
        self.assertNotEqual(a, ca.assinatura_estado_pets({1: 3, 2: 1}, {1: 11}))

    def test_nome_do_proprio_personagem_no_login(self):
        nome = 'Vitória'.encode('utf-8')
        payload = (varint(2445) + bytes.fromhex('5f 02 eb 08 37') +
                   varint(len(nome)) + nome + struct.pack('<HB', 11002, 14))
        eventos = []
        pp.Decoder(eventos.append).segment('jogo', 1000,
                                            frame(pp.OP_NICKNAME, payload))
        self.assertEqual(eventos, [('ready',), ('nickname', 'Vitória')])
        com_flag = varint(len(payload) + 7) + b'\xf0' + pp.OP_NICKNAME + payload
        outros = []
        pp.Decoder(outros.append).segment('jogo', 2000, com_flag)
        self.assertEqual(outros, [('ready',), ('nickname', 'Vitória')])
        self.assertIsNone(pp.parse_nickname(payload[:-2]))
        self.assertIsNone(pp.parse_nickname(varint(2445) + b'\x00' * 5 +
                                             b'\x05A B C' + struct.pack('<HB', 11002, 14)))

    def test_varias_almas_no_mesmo_evento_e_retransmissao(self):
        ganhos = []
        dec = pp.Decoder(ganhos.append)
        payload = b'\x02' + struct.pack('<II', 1036, 3) + struct.pack('<II', 1202, 2)
        data = frame(pp.OP_GAIN, payload)
        dec.segment('jogo', 1000, data[:5])
        self.assertEqual(ganhos, [])
        dec.segment('jogo', 1005, data[5:])
        dec.segment('jogo', 1000, data)  # retransmissao TCP
        self.assertEqual(ganhos, [('ready',), ('gain', 1036, 3), ('gain', 1202, 2)])

    def test_rajada_comprimida(self):
        raw = frame(pp.OP_GAIN, b'\x01' + struct.pack('<II', 1036, 1)) * 3
        # Bloco LZ4 valido composto apenas de literais.
        packed = bytes([0xF0, len(raw) - 15]) + raw
        data = frame(pp.OP_LZ4, struct.pack('<I', len(raw)) + packed)
        ganhos = []
        pp.Decoder(ganhos.append).segment('jogo', 1000, data)
        self.assertEqual(ganhos, [('ready',)] + [('gain', 1036, 1)] * 3)

    def test_estado_do_login_nao_e_drop(self):
        payload = (b'\x00' * 4 + varint(2) +
                   struct.pack('<III', 1036, 1036, 1) +
                   struct.pack('<III', 1202, 1202, 2) +
                   varint(2) + struct.pack('<II', 1036, 4) +
                   struct.pack('<II', 1202, 7))
        eventos = []
        pp.Decoder(eventos.append).segment('jogo', 1000, frame(pp.OP_STATE, payload))
        self.assertEqual(eventos, [('ready',),
                                  ('state', {1036: 1, 1202: 2}, {1036: 4, 1202: 7})])

    def test_ids_parecidos_nao_misturam_contagem(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            p = ca.Progresso(os.path.join(pasta, 'progresso.json'))
            p.somar_pet(1036, 'Lupyllini', 3)
            p.somar_pet(1202, 'Lupyllini Putrefato', 2)
            p.somar_pet(1036, 'Lupyllini', 1)
            self.assertEqual({m['pet_id']: m['qtd'] for m in p.monstros},
                             {'1036': 4, '1202': 2})
            self.assertTrue(p.sincronizar_pets({1036: 1, 1202: 2},
                                               {1036: 4, 1202: 7},
                                               {'1036': 'Lupyllini',
                                                '1202': 'Lupyllini Putrefato'}))
            self.assertEqual({m['pet_id']: m['qtd'] for m in p.monstros},
                             {'1036': 9, '1202': 37})

    def test_login_importa_progresso_antigo_sem_contar_drop_ou_reabrir_fechado(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            caminho = os.path.join(pasta, 'progresso.json')
            p = ca.Progresso(caminho)
            nomes = {'1036': 'Lupyllini', '1202': 'Lupyllini Putrefato'}
            p.somar_pet(1036, 'Lupyllini', 1)
            self.assertTrue(p.sincronizar_pets({1036: 1, 1202: 2},
                                               {1036: 19, 1202: 7}, nomes))
            self.assertEqual({m['pet_id']: m['qtd'] for m in p.monstros},
                             {'1036': 24})
            self.assertEqual(sum(sum(v.values()) for v in p.dias.values()), 1)
            self.assertEqual(len(p.catalogo_pets(nomes)), 2)
            self.assertFalse(p.sincronizar_pets({1036: 1, 1202: 2},
                                                {1036: 19, 1202: 7}, nomes))
            p.remover(next(m for m in p.monstros if m['pet_id'] == '1036'))
            p.salvar()
            p = ca.Progresso(caminho)
            self.assertFalse(p.sincronizar_pets({1036: 1, 1202: 2},
                                                {1036: 19, 1202: 7}, nomes))
            self.assertEqual(p.monstros, [])
            p.somar_pet(1036, 'Lupyllini', 1)
            self.assertEqual(p.monstros[-1]['qtd'], 25)

    def test_primeira_alma_na_lista_parte_do_total_sincronizado_da_conta(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            p = ca.Progresso(os.path.join(pasta, 'progresso.json'))
            nomes = {'1142': 'Aberração Deformada'}
            self.assertTrue(p.sincronizar_pets({1142: 2}, {1142: 44}, nomes))
            self.assertEqual(p.monstros, [])
            m = p.somar_pet(1142, nomes['1142'], 1)
            self.assertEqual(m['qtd'], 75)
            self.assertEqual(p.pet_estado['1142'], 75)
            linha = next(r for r in p.catalogo_pets(nomes) if r[0] == '1142')
            self.assertEqual(linha[2:], ('Nv.2 45/75', '30', 'jogo'))

    def test_catalogo_antigo_sai_da_aba_de_farm_sem_perder_os_valores(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            caminho = os.path.join(pasta, 'progresso.json')
            with open(caminho, 'w', encoding='utf-8') as f:
                json.dump({'monstros': [
                    {'pet_id': '1036', 'nome': 'Lupyllini', 'qtd': 24,
                     'apelidos': [], 'atualizado': 0},
                    {'pet_id': '1142', 'nome': 'Aberração Deformada', 'qtd': 74,
                     'apelidos': [], 'atualizado': 123}],
                }, f)
            p = ca.Progresso(caminho)
            self.assertEqual([m['pet_id'] for m in p.monstros], ['1142'])
            linhas = {r[0]: r for r in p.catalogo_pets(
                {'1036': 'Lupyllini', '1142': 'Aberração Deformada'})}
            self.assertEqual(linhas['1036'][2:], ('Nv.1 19/25', '81', 'salvo'))
            self.assertEqual(linhas['1142'][2:], ('Nv.2 44/75', '31', 'salvo'))

    def test_catalogo_lista_todos_e_distingue_valor_salvo_do_jogo(self):
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            caminho = os.path.join(pasta, 'progresso.json')
            nomes = {'1142': 'Aberração Deformada', '1036': 'Lupyllini',
                     '1202': 'Lupyllini Putrefato'}
            p = ca.Progresso(caminho)
            self.assertEqual(len(p.catalogo_pets(nomes)), 3)
            self.assertEqual(next(r for r in p.catalogo_pets(nomes) if r[0] == '1202')[2:],
                             ('—', '—', 'desconhecido'))
            p.somar_pet(1142, nomes['1142'], 19)
            self.assertEqual(next(r for r in p.catalogo_pets(nomes) if r[0] == '1142')[2:],
                             ('Nv.1 14/25', '86', 'salvo'))
            p.sincronizar_pets({1142: 2}, {1142: 44}, nomes)
            p.salvar()
            p = ca.Progresso(caminho)
            rows = {r[0]: r for r in p.catalogo_pets(nomes)}
            self.assertEqual(rows['1142'][2:], ('Nv.2 44/75', '31', 'jogo'))
            self.assertEqual(rows['1202'][2:], ('Nv.0 0/5', '105', 'jogo'))
            p.somar_pet(1142, nomes['1142'], 2)
            self.assertEqual(next(r for r in p.catalogo_pets(nomes) if r[0] == '1142')[2:],
                             ('Nv.2 46/75', '29', 'jogo'))

    def test_atualizador_instala_arquivos_novos_sem_apagar_progresso(self):
        pasta_codigo = os.path.dirname(os.path.dirname(__file__))
        with tempfile.TemporaryDirectory(dir=os.path.dirname(__file__)) as pasta:
            with open(os.path.join(pasta, 'progresso.json'), 'w') as f:
                f.write('{"monstros": []}')
            with open(os.path.join(pasta, 'contador_almas.py'), 'w') as f:
                f.write('VERSAO = "1.8.8"\n')
            with open(os.path.join(pasta, 'requirements.txt'), 'w') as f:
                f.write('mss>=9.0\n')

            def baixar(url):
                nome = url.rsplit('/', 1)[-1]
                with open(os.path.join(pasta_codigo, nome), 'rb') as f:
                    return f.read()

            with open(os.path.join(pasta_codigo, 'contador_almas.py'), 'rb') as f:
                codigo = f.read()
            with patch.object(ca, 'baixar_texto', side_effect=baixar):
                ca.aplicar_atualizacao(codigo, pasta)
            self.assertTrue(os.path.isfile(os.path.join(pasta, 'pet_protocol.py')))
            self.assertTrue(os.path.isfile(os.path.join(pasta, 'pet_nomes_npcap.json')))
            with open(os.path.join(pasta, 'progresso.json')) as f:
                self.assertEqual(f.read(), '{"monstros": []}')


if __name__ == '__main__':
    unittest.main()
