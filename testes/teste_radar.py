import struct
import unittest

import pet_protocol
from contador_almas import linhas_radar, locais_radar


class RadarTest(unittest.TestCase):
    def test_known_farm_location_is_distinct_from_live_mob(self):
        catalogo = [("1028", "Aberração de Krao", "Nv.2 44/75", "31", "jogo")]
        especies = {"2000004": [1028, "Krao"]}
        pontos = {"2000004": [[1000, -2000], [90000, 0]]}
        linhas = locais_radar(pontos, (1000, 0, 100), catalogo, especies, 150)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0][:5], ("Krao", "Aberração de Krao", 31, 20, "N"))

    def test_zone_position(self):
        payload = b"\0" * 4 + struct.pack("<I", 1010) + b"\0" * 8
        payload += struct.pack("<ff", 5000, -3000)
        self.assertEqual(pet_protocol.parse_zone(payload), (1010, 5000, -3000))

    def test_spawn_and_remove(self):
        payload = bytes([123, 0, 0, 0]) + struct.pack("<I", 2000004)
        payload += b"\0\0" + struct.pack("<ff", 1200, -300)
        self.assertEqual(pet_protocol.parse_npc_spawn(payload),
                         (123, 2000004, 1200, -300))
        self.assertEqual(pet_protocol.parse_npc_remove(bytes([123, 0, 3])), 123)

    def test_position_requires_own_entity(self):
        payload = bytes([123, 3]) + struct.pack("<ff", 1000, 2000)
        self.assertEqual(pet_protocol.parse_local_position(payload, 123), (1000, 2000))
        self.assertIsNone(pet_protocol.parse_local_position(payload, 124))

    def test_effect_updates_only_own_position(self):
        payload = bytes([7, 1, 0, 1])
        payload += struct.pack("<IIIQ", 1000, 5000, 0, 0)
        payload += bytes([123, 0]) + struct.pack("<fff", 1000, 2000, 300)
        self.assertEqual(pet_protocol.parse_effect_position(payload, 123),
                         (1000, 2000))
        self.assertIsNone(pet_protocol.parse_effect_position(payload, 124))

    def test_only_missing_pet_and_recent_position(self):
        now = 500.0
        mobs = {1: (2000004, 1000, -2000, now),
                2: (2000005, 1000, 2000, now)}
        catalogo = [("1028", "Aberração de Krao", "Nv.2 44/75", "31", "jogo"),
                    ("1029", "Batedor Klaw", "Nv.3 MAX", "0", "jogo")]
        especies = {"2000004": [1028, "Krao"], "2000005": [1029, "Klaw"]}
        linhas = linhas_radar(mobs, (1000, 0, now), catalogo, especies, now)
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0][:3], ("Krao", "Aberração de Krao", 31))
        self.assertEqual(linhas[0][3:5], (20, "N"))
        velhas = linhas_radar(mobs, (1000, 0, now - 11), catalogo, especies, now)
        self.assertEqual(velhas[0][3:5], (None, None))


if __name__ == "__main__":
    unittest.main()
