"""Aion 2 pet soul protocol: passive capture, decoding and progress tracking.

Adapted from elkaida/AION2-pet-tracker (MIT, see LICENSE). Only the parts the
overlay needs are kept: TCP stream reassembly, frame decoding and the two pet
opcodes. Read-only: nothing is ever sent to the game or its servers.

Wire format (server -> client):
    frame  = varint(len) + opcode(2) + payload, where len = opcode + payload + 4
    0xFFFF = LZ4 bundle: uint32 uncompressed size + raw LZ4 block of more frames
    0x0090 = full pet state, sent on entering the world (levels + points)
    0x0D90 = souls gained on a kill (pet id + amount, no running total)
"""
import json
import hashlib
import os
import struct
import sys
import threading
import time

FROZEN = getattr(sys, "frozen", False)  # running as the PyInstaller exe
RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))  # bundled read-only files


def _app_dir():
    """Next to the exe/scripts; falls back to %LOCALAPPDATA% if that folder isn't writable."""
    here = os.path.dirname(os.path.abspath(sys.executable if FROZEN else __file__))
    try:
        probe = os.path.join(here, ".write_test")
        open(probe, "w").close()
        os.remove(probe)
        return here
    except OSError:
        fallback = os.path.join(os.environ.get("LOCALAPPDATA", here), "Aion2PetOverlay")
        os.makedirs(fallback, exist_ok=True)
        return fallback


APP_DIR = _app_dir()
if sys.stderr is None:  # windowed exe / pythonw: libraries still print warnings
    sys.stderr = sys.stdout = open(os.devnull, "w")
STATE_FILE = os.path.join(APP_DIR, "pet_state.json")
LEGACY_STATE_FILE = os.path.join(APP_DIR, "aion2_pet_tracker_state.json")
LOG_FILE = os.path.join(APP_DIR, "pet_overlay.log")
INVENTORY_STATE_FILE = os.path.join(APP_DIR, "inventory_state.json")

REQ = [5, 25, 75]  # souls needed for levels 1, 2 and 3 (counter resets each level)
MAX_LEVEL = len(REQ)

OP_STATE = b"\x00\x90"
OP_GAIN = b"\x0d\x90"
OP_ABYSS_PROGRESS = b"\x84\x56"
OP_ABYSS_SNAPSHOT = b"\x83\x56"
OP_INVENTORY_CHANGE = b"\x1b\x56"
ABYSS_POINT_ACHIEVEMENT = 230111500
KINAH_ITEM_ID = 930100001
OD_ITEM_TYPES = {
    610610013: "OD",
    610610014: "OD Fino",
    610610015: "OD Puro",
    610610016: "OD Radiante",
}
# IDs de materiais de coleta do catálogo do jogo. O nome permanece em inglês
# quando é assim que a base de itens o identifica.
GATHER_ITEM_TYPES = {}
for _item, _name in enumerate(("Orichalcum Ore", "Fine Orichalcum Ore",
                               "Pure Orichalcum Ore", "Radiant Orichalcum Ore"), 610620013):
    GATHER_ITEM_TYPES[_item] = ("Ore", _name)
for _item, _name in enumerate(("Sapphire Gemstone", "Diamond Gemstone", "Ruby Gemstone",
                               "Fine Sapphire Gemstone", "Fine Diamond Gemstone",
                               "Fine Ruby Gemstone", "Pure Sapphire Gemstone",
                               "Pure Diamond Gemstone", "Pure Ruby Gemstone",
                               "Radiant Sapphire Gemstone", "Radiant Diamond Gemstone",
                               "Radiant Ruby Gemstone"), 610660025):
    GATHER_ITEM_TYPES[_item] = ("Gem", _name)
for _item, _name in zip((610650011, 610650012, 610650013, 610650014,
                         610650015, 610650016, 610650017, 610650018),
                        ("Yggdrasil Log", "Asvata Log", "Fine Yggdrasil Log",
                         "Fine Asvata Log", "Pure Yggdrasil Log", "Pure Asvata Log",
                         "Radiant Yggdrasil Log", "Radiant Asvata Log")):
    GATHER_ITEM_TYPES[_item] = ("Log", _name)
for _item, _name in enumerate(("Aria", "Targena", "Azpha", "Calendula",
                               "Fresh Aria", "Fresh Targena", "Fresh Azpha",
                               "Fresh Calendula"), 610670041):
    GATHER_ITEM_TYPES[_item] = ("Herb", _name)
GATHER_ITEM_TYPES.update({
    610680043: ("Ingredient", "Coriolus"),
    610680044: ("Ingredient", "Kukuru"),
    610680045: ("Ingredient", "Mela"),
    610680047: ("Ingredient", "Inina"),
    610680048: ("Ingredient", "Cypri"),
    610680049: ("Ingredient", "Morfa"),
    610680050: ("Ingredient", "Ohkra"),
    610680051: ("Ingredient", "Raydam"),
    610680053: ("Ingredient", "Conide"),
    610680054: ("Ingredient", "Pujery"),
})
HEAL_SKILL_BASES = {
    10000000, 11110000, 11120000, 11260000, 11270000, 11380000, 11720000,
    11730000, 12110000, 12230000, 12250000, 12260000, 12320000, 12720000,
    13080000, 13260000, 13710000, 13790000, 14260000, 14710000, 14790000,
    15240000, 15410000, 15790000, 16190000, 16200000, 16270000, 16750000,
    16770000, 16790000, 16990000, 17090000, 17100000, 17120000, 17160000,
    17240000, 17270000, 17290000, 17320000, 17390000, 17720000, 17800000,
    18120000, 18140000, 18160000, 18170000, 18200000, 18240000, 18420000,
    18440000, 18720000, 19350000, 19360000, 19450000, 19710000, 19790000,
}
OP_NICKNAME = b"\x33\x36"
OP_ZONE = b"\x21\x36"
OP_NPC_SPAWN = b"\x41\x36"
OP_NPC_REMOVE = b"\x42\x36"
OP_LOCAL_POSITION = b"\x3c\x37"
OP_EFFECT = b"\x2a\x38"
OP_EFFECT_RENEWED = b"\x2b\x38"
OP_DAMAGE = b"\x04\x38"
OP_DOT = b"\x05\x38"
OP_PLAYER_NICKNAME = b"\x45\x36"
OP_PARTY_ROSTER = b"\x02\x97"
OP_LZ4 = b"\xff\xff"

# Opcodes seen in the stream. Used to find the next frame boundary after a lost
# segment; grows as new opcodes show up.
KNOWN_OPS = {bytes.fromhex(h) for h in (
    "0036 1d37 1c37 0438 008d 0238 0638 1a37 1b37 4b36 0338 0138 3b38 3d38 2a37 0191 2937 8356 8456 "
    "2a38 218d ffff 2b38 2c38 1b56 3538 4738 4136 1838 1938 0336 3438 3138 1f56 4a36 3436 4236 "
    "1e56 4736 1f37 2156 2037 0d90 3237 048d 2237 3536 0090 3336 2136 3c37 4236").split()}
KNOWN_OPS.update((OP_DAMAGE, OP_DOT, OP_PLAYER_NICKNAME, OP_PARTY_ROSTER))


def log(*parts):
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + " ".join(map(str, parts)) + "\n")


def npcap_installed():
    npcap_dir = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Npcap")
    return os.path.isfile(os.path.join(npcap_dir, "wpcap.dll"))


# ---------------------------------------------------------------- decoding

def read_varint(buf, i):
    value = shift = 0
    while True:
        if i >= len(buf):
            raise IndexError
        byte = buf[i]
        i += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, i


def split_frames(buf):
    """Cuts a buffer into frames. Returns (frames, rest), or (frames, None) on garbage."""
    i, frames = 0, []
    while i < len(buf):
        try:
            n, j = read_varint(buf, i)
        except IndexError:
            break
        total = n - 4 + (j - i)
        if total < (j - i) + 2 or total > 4_000_000:
            return frames, None
        if i + total > len(buf):
            break
        frames.append(buf[i:i + total])
        i += total
    return frames, buf[i:]


def frame_op(frame):
    _, j = read_varint(frame, 0)
    if j < len(frame) and 0xF0 <= frame[j] < 0xFF:
        j += 1
    return frame[j:j + 2]


def decompress_lz4_block(data, expected_size):
    """Decodifica um bloco LZ4 sem depender de extensoes C do Python."""
    if not 0 <= expected_size <= 4_000_000:
        raise ValueError("invalid LZ4 size")
    out = bytearray()
    i = 0
    while i < len(data):
        token = data[i]
        i += 1
        literal_count = token >> 4
        if literal_count == 15:
            while True:
                extra = data[i]
                i += 1
                literal_count += extra
                if extra != 255:
                    break
        if i + literal_count > len(data):
            raise ValueError("truncated LZ4 literals")
        out.extend(data[i:i + literal_count])
        i += literal_count
        if i == len(data):
            break
        if i + 2 > len(data):
            raise ValueError("truncated LZ4 offset")
        offset = data[i] | data[i + 1] << 8
        i += 2
        if not 1 <= offset <= len(out):
            raise ValueError("invalid LZ4 offset")
        match_count = (token & 15) + 4
        if token & 15 == 15:
            while True:
                extra = data[i]
                i += 1
                match_count += extra
                if extra != 255:
                    break
        for _ in range(match_count):
            out.append(out[-offset])
        if len(out) > expected_size:
            raise ValueError("LZ4 output larger than declared")
    if len(out) != expected_size:
        raise ValueError("LZ4 output size mismatch")
    return bytes(out)


def unpack(frame):
    """Yields (opcode, payload), expanding compressed bundles."""
    _, j = read_varint(frame, 0)
    if j < len(frame) and 0xF0 <= frame[j] < 0xFF:
        j += 1
    op, body = frame[j:j + 2], frame[j + 2:]
    if op != OP_LZ4:
        yield op, body
        return
    size = int.from_bytes(body[:4], "little")
    try:
        raw = decompress_lz4_block(body[4:], size)
    except Exception as e:
        log("lz4:", e)
        return
    inner, _ = split_frames(raw)
    for f in inner:
        yield from unpack(f)


def parse_state(payload):
    i = 4  # uint32 header, meaning unknown
    count, i = read_varint(payload, i)
    levels = {}
    for _ in range(count):
        pid, pid2, level = struct.unpack_from("<III", payload, i)
        i += 12
        if pid != pid2 or level > 10:
            raise ValueError("unexpected level entry")
        levels[pid] = level
    count, i = read_varint(payload, i)
    points = {}
    for _ in range(count):
        pid, pts = struct.unpack_from("<II", payload, i)
        i += 8
        points[pid] = pts
    return levels, points


def parse_gain(payload):
    count = payload[0]
    if not 1 <= count <= 100 or len(payload) < 1 + count * 8:
        raise ValueError("invalid soul gain payload")
    return [struct.unpack_from("<II", payload, 1 + i * 8)
            for i in range(count)]


def parse_abyss_progress(payload):
    """Retorna o total atual de AP do evento de conquista GetItem (0x8456)."""
    if not payload:
        return None
    count = payload[0]
    if len(payload) != 1 + count * 17:
        return None
    for i in range(count):
        offset = 1 + i * 17
        achievement, total = struct.unpack_from("<II", payload, offset)
        if achievement == ABYSS_POINT_ACHIEVEMENT:
            return total
    return None


def parse_abyss_snapshot(payload):
    """Total já acumulado no snapshot de conquistas enviado ao entrar no jogo."""
    try:
        count, offset = read_varint(payload, 0)
    except IndexError:
        return None
    if count > 100_000 or len(payload) != offset + count * 17:
        return None
    for i in range(count):
        record = offset + i * 17
        achievement, total = struct.unpack_from("<II", payload, record)
        if achievement == ABYSS_POINT_ACHIEVEMENT:
            return total
    return None


def parse_inventory_change(payload):
    """Extrai o item, tipo de alteração, instância e quantidade do 0x1b56."""
    # marcador, kind u8, instância u32, item u32, quantidade u64
    if len(payload) < 23 or payload[0] != 1 or payload[1] not in (0, 1, 2):
        return None
    item = struct.unpack_from("<I", payload, 11)[0]
    instance = struct.unpack_from("<I", payload, 3)[0]
    quantity = struct.unpack_from("<Q", payload, 15)[0]
    return payload[1], instance, item, quantity


def parse_damage(payload):
    """Dano direto: alvo, flags, atacante, skill e quantidade variável."""
    try:
        target, p = read_varint(payload, 0)
        if p + 2 > len(payload):
            return None
        flags = payload[p]
        p += 2
        if not flags & 0x04:
            return None
        actor, p = read_varint(payload, p)
        if p + 6 > len(payload):
            return None
        skill = struct.unpack_from("<I", payload, p)[0]
        p += 4
        marker = next((i for i in range(p + 2, len(payload) - 3)
                       if 1 <= payload[i] <= 9 and payload[i + 1:i + 4] == b"\0\0\0"), -1)
        if marker < 0:
            return None
        q = marker + 4
        _, q = read_varint(payload, q)
        amount, q = read_varint(payload, q)
        if not (0 < actor < 10_000_000 and 0 < target < 10_000_000 and
                0 < skill < 300_000_000 and 0 < amount <= 10_000_000_000):
            return None
        return actor, target, amount, skill
    except (IndexError, struct.error, ValueError):
        return None


def parse_dot_damage(payload):
    """Tick de dano contínuo. Ticks com flag de cura não entram no DPS."""
    try:
        target, p = read_varint(payload, 0)
        flags = payload[p]
        p += 1
        if flags & 0x01 or flags & 0x0a != 0x0a:
            return None
        actor, p = read_varint(payload, p)
        _, p = read_varint(payload, p)
        if p + 4 > len(payload):
            return None
        p += 4
        amount, p = read_varint(payload, p)
        if len(payload) != p + 4 or not (0 < actor < 10_000_000 and
                                         0 < target < 10_000_000 and
                                         0 < amount <= 10_000_000_000):
            return None
        skill = struct.unpack_from("<I", payload, p)[0]
        return actor, target, amount, skill
    except (IndexError, struct.error, ValueError):
        return None


def _network_name(payload, start, minimum=2):
    if not 0 <= start < len(payload):
        return None
    size = payload[start]
    if not minimum <= size <= 24 or start + 1 + size > len(payload):
        return None
    try:
        name = payload[start + 1:start + 1 + size].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return name if all(c.isalnum() for c in name) else None


def parse_player_nickname(payload):
    try:
        entity, p = read_varint(payload, 0)
        for at in range(p, min(len(payload) - 3, p + 24)):
            name = _network_name(payload, at, 3)
            if name:
                return entity, name
    except IndexError:
        pass
    return None


def parse_party_roster(payload):
    members = []
    i = 0
    while i + 1 < len(payload):
        name = _network_name(payload, i, 2)
        if not name:
            i += 1
            continue
        after = i + 1 + payload[i]
        if (after + 8 <= len(payload) and payload[after] != 0 and
                payload[after + 1:after + 4] == b"\0\0\0" and
                1 <= payload[after + 4] <= 60 and
                payload[after + 5:after + 8] == b"\0\0\0"):
            members.append(name)
            i = after + 8
        else:
            i += 1
    return list(dict.fromkeys(members))


def parse_summon_owner(payload):
    try:
        entity, p = read_varint(payload, 0)
        owner_name = _network_name(payload, p + 3, 2) if p + 2 < len(payload) and payload[p + 2] == 1 else None
        marker = payload.find(b"\xff" * 8, p + 4)
        owner_id = None
        if marker >= 0:
            q = marker + 16
            owner, _ = read_varint(payload, q)
            if owner > 0 and owner != entity:
                owner_id = owner
        return entity, owner_id, owner_name
    except (IndexError, ValueError):
        return None


def parse_nickname(payload):
    """Nome do personagem local em 0x3336; ignora outras formas desse opcode."""
    try:
        entity, i = read_varint(payload, 0)
        if not 0 < entity < 1_000_000 or i + 5 >= len(payload):
            return None
        i += 5  # preambulo da ficha do proprio personagem
        tamanho, i = read_varint(payload, i)
        if not 1 <= tamanho <= 71 or i + tamanho + 3 > len(payload):
            return None
        nome = payload[i:i + tamanho].decode("utf-8")
        if not (1 <= len(nome) <= 40 and any(c.isalpha() for c in nome) and
                all(c.isalnum() or c in "_-" for c in nome)):
            return None
        i += tamanho
        servidor = struct.unpack_from("<H", payload, i)[0]
        classe = payload[i + 2]
        if not servidor or classe > 32:
            return None
        return nome
    except (IndexError, UnicodeDecodeError, ValueError, struct.error):
        return None


def parse_zone(payload):
    """Zona e posição de chegada; coordenadas do jogo têm 100 unidades por metro."""
    if len(payload) < 24:
        return None
    zone = struct.unpack_from("<I", payload, 4)[0]
    x, y = struct.unpack_from("<ff", payload, 16)
    if zone <= 0 or not all(-500_000 < v < 500_000 for v in (x, y)):
        return None
    return zone, x, y


def parse_npc_spawn(payload):
    """Aparição de monstro: instância, tipo e posição observada pelo jogo."""
    import math
    try:
        entity, p = read_varint(payload, 0)
        if not 0 < entity < 1_000_000 or len(payload) < p + 17:
            return None
        if payload[p + 2] == 1:
            size = payload[p + 3]
            if not 1 <= size <= 100 or p + 4 + size + 14 > len(payload):
                return None
            p += 1 + size
        p += 3
        npc = struct.unpack_from("<I", payload, p)[0]
        x, y = struct.unpack_from("<ff", payload, p + 6)
        if not 0 < npc < 10_000_000 or not all(math.isfinite(v) and abs(v) < 500_000
                                                for v in (x, y)):
            return None
        return entity, npc, x, y
    except (IndexError, struct.error, ValueError):
        return None


def parse_spawn_max_hp(payload):
    """HP máximo anunciado no spawn; bosses de DG usam este valor."""
    trailer = b"\x64\0\0\0\x64\0\0\0"
    try:
        _, p = read_varint(payload, 0)
        if len(payload) < p + 7:
            return None
        if payload[p + 2] == 1:
            size = payload[p + 3]
            if not 1 <= size <= 100 or p + 4 + size + 7 > len(payload):
                return None
            p += 1 + size
        p += 3
        after_code = p + 4
        for skip in (21, 33):
            q = after_code + skip
            if q >= len(payload):
                continue
            current, q = read_varint(payload, q)
            maximum, q = read_varint(payload, q)
            if (0 <= current <= maximum and maximum > 0 and
                    q + 8 <= len(payload) and payload[q:q + 8] == trailer):
                return maximum
    except (IndexError, ValueError):
        pass
    return None


def parse_npc_remove(payload):
    try:
        entity, p = read_varint(payload, 0)
        return entity if 0 < entity < 1_000_000 and len(payload) == p + 2 else None
    except IndexError:
        return None


def parse_local_position(payload, own_entity):
    import math
    if own_entity is None:
        return None
    try:
        entity, p = read_varint(payload, 0)
        if entity != own_entity or len(payload) < p + 9 or payload[p] != 3:
            return None
        x, y = struct.unpack_from("<ff", payload, p + 1)
        return (x, y) if all(math.isfinite(v) and abs(v) < 500_000 for v in (x, y)) else None
    except (IndexError, struct.error):
        return None


def parse_effect_position(payload, own_entity, renewed=False):
    """Posição do próprio personagem no fim de um efeito que ele lançou."""
    import math
    if own_entity is None:
        return None
    try:
        _, p = read_varint(payload, 0)  # alvo
        if not renewed:
            if payload[p] != 1:
                return None
            p += 1
        p += 1  # tipo
        _, p = read_varint(payload, p)  # instância do efeito
        if p + 20 >= len(payload):
            return None
        efeito = struct.unpack_from("<I", payload, p)[0]
        p += 20  # efeito, duração, flag e relógio do servidor
        caster, p = read_varint(payload, p)
        if caster != own_entity or not 10 <= efeito <= 2_999_999_999 or len(payload) - 12 <= p:
            return None
        x, y, z = struct.unpack_from("<fff", payload, len(payload) - 12)
        if not all(math.isfinite(v) for v in (x, y, z)) or not 100 < abs(x) < 500_000 or not 100 < abs(y) < 500_000 or abs(z) >= 50_000:
            return None
        return x, y
    except (IndexError, struct.error):
        return None


def find_sync(buf, need=3):
    """Offset where `need` consecutive frames with known opcodes start
    (or known frames that end exactly at the end of the buffer)."""
    n = len(buf)
    for off in range(n):
        i, good = off, 0
        while good < need:
            try:
                length, j = read_varint(buf, i)
            except IndexError:
                break
            total = length - 4 + (j - i)
            op_off = j + (1 if j < n and 0xF0 <= buf[j] < 0xFF else 0)
            if total < (op_off - i) + 2 or buf[op_off:op_off + 2] not in KNOWN_OPS:
                good = -1
                break
            if i + total > n:
                break
            i += total
            good += 1
        if good >= need or (good > 0 and i == n):
            return off
    return None


class Stream:
    """Reassembles one server->client TCP stream, tolerating retransmits and reordering."""
    MASK, HALF = 0xFFFFFFFF, 0x80000000

    def __init__(self):
        self.next = None
        self.pending = {}
        self.buf = b""
        self.resync = True  # capture may have started mid-frame

    def add(self, seq, data):
        if self.next is None:
            self.next = seq
        if len(data) > len(self.pending.get(seq, b"")):
            self.pending[seq] = data
        out = bytearray()
        moved = True
        while moved:
            moved = False
            for s in list(self.pending):
                d = (s - self.next) & self.MASK
                if d != 0 and d < self.HALF:
                    continue  # still in the future
                chunk = self.pending.pop(s)
                skip = 0 if d == 0 else (self.next - s) & self.MASK
                if skip < len(chunk):
                    out += chunk[skip:]
                    self.next = (self.next + len(chunk) - skip) & self.MASK
                    moved = True
        if len(self.pending) > 8:  # a segment was lost by the capture: jump the hole
            log("gap, resync")
            self.buf, self.resync = b"", True
            self.next = min(self.pending, key=lambda s: (s - self.next) & self.MASK)
            return self.add(self.next, self.pending.pop(self.next))
        self.buf += bytes(out)
        if self.resync:
            off = find_sync(self.buf)
            if off is None:
                if len(self.buf) > 65536:
                    self.buf = b""
                return []
            self.buf, self.resync = self.buf[off:], False
        frames, rest = split_frames(self.buf)
        if rest is None:
            self.buf, self.resync = b"", True
            return frames
        self.buf = rest
        for f in frames:
            KNOWN_OPS.add(frame_op(f))
        return frames


class Decoder:
    """Raw TCP segments -> soul, collection and own-character events."""

    def __init__(self, emit, inventory_state_file=None):
        self.emit = emit
        self.streams = {}
        self.ready = False
        self.own_entity = None
        self.zone_id = None
        self.abyss_progress = None
        self.entity_names = {}
        self.party_names = set()
        self.summon_owners = {}
        self.own_name = None
        self.boss_entities = set()
        self.current_boss = None
        self.inventory_state_file = inventory_state_file
        self.inventory_instances = {}
        if inventory_state_file:
            try:
                with open(inventory_state_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                self.inventory_instances = {
                    int(instance): (int(values[0]), int(values[1]))
                    for instance, values in saved.items()
                    if isinstance(values, list) and len(values) == 2
                }
            except (OSError, ValueError, TypeError):
                pass

    def _save_inventory(self):
        if not self.inventory_state_file:
            return
        try:
            temporary = self.inventory_state_file + ".tmp"
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump({str(k): list(v) for k, v in self.inventory_instances.items()}, f)
            os.replace(temporary, self.inventory_state_file)
        except OSError as e:
            log("inventory state save:", type(e).__name__)

    def segment(self, conn, seq, data):
        stream = self.streams.setdefault(conn, Stream())
        for frame in stream.add(seq, data):
            if not self.ready:
                self.ready = True
                self.emit(("ready",))
            try:
                for op, payload in unpack(frame):
                    if op == OP_GAIN:
                        for pid, amount in parse_gain(payload):
                            if 0 < amount <= 100:
                                self.emit(("gain", pid, amount))
                    elif op == OP_STATE:
                        try:
                            levels, points = parse_state(payload)
                            log("state decoded", "levels", len(levels),
                                "points", len(points))
                            self.emit(("state", levels, points))
                        except Exception as e:
                            log("state parse:", type(e).__name__)
                    elif op == OP_ABYSS_PROGRESS:
                        total = parse_abyss_progress(payload)
                        if total is not None:
                            previous = self.abyss_progress
                            self.abyss_progress = total
                            if previous is not None and total > previous:
                                self.emit(("abyss_points", total - previous))
                    elif op == OP_ABYSS_SNAPSHOT:
                        total = parse_abyss_snapshot(payload)
                        if total is not None:
                            self.abyss_progress = total
                    elif op == OP_INVENTORY_CHANGE:
                        change = parse_inventory_change(payload)
                        if change:
                            kind, instance, item, quantity = change
                            previous = self.inventory_instances.get(instance)
                            if kind == 2 or quantity == 0:
                                self.inventory_instances.pop(instance, None)
                                self._save_inventory()
                            else:
                                self.inventory_instances[instance] = (item, quantity)
                                if item in OD_ITEM_TYPES or item in GATHER_ITEM_TYPES:
                                    self._save_inventory()
                                old_quantity = previous[1] if previous and previous[0] == item else None
                                tracked_collection = item in OD_ITEM_TYPES or item in GATHER_ITEM_TYPES
                                ganho = quantity if kind == 0 else (
                                    quantity - old_quantity if old_quantity is not None else (
                                        1 if kind == 1 and tracked_collection else 0))
                                if item == KINAH_ITEM_ID and ganho > 0:
                                    self.emit(("kinah_gain", ganho))
                                elif item in OD_ITEM_TYPES and ganho > 0:
                                    self.emit(("od_gain", OD_ITEM_TYPES[item], ganho))
                                elif item in GATHER_ITEM_TYPES and ganho > 0:
                                    self.emit(("gather_gain", item, ganho))
                    elif op == OP_NICKNAME:
                        nome = parse_nickname(payload)
                        if nome:
                            self.own_entity, _ = read_varint(payload, 0)
                            self.own_name = nome
                            self.entity_names[self.own_entity] = nome
                            log("nickname decoded", nome)
                            self.emit(("nickname", nome))
                            if not self.party_names:
                                self.emit(("dps_party", [nome]))
                        else:
                            log("nickname ignored", "payload_len", len(payload),
                                "prefix", payload[:8].hex())
                    elif op == OP_PLAYER_NICKNAME:
                        player = parse_player_nickname(payload)
                        if player:
                            self.entity_names[player[0]] = player[1]
                    elif op == OP_PARTY_ROSTER:
                        members = parse_party_roster(payload)
                        self.party_names = set(members)
                        shown = members or ([self.own_name] if self.own_name else [])
                        if shown:
                            self.emit(("dps_party", shown))
                    elif op in (OP_DAMAGE, OP_DOT):
                        hit = parse_damage(payload) if op == OP_DAMAGE else parse_dot_damage(payload)
                        if hit:
                            actor, target, amount, skill = hit
                            if skill // 10000 * 10000 in HEAL_SKILL_BASES or 2_000_000 <= skill < 3_000_000:
                                continue
                            owner = self.summon_owners.get(actor)
                            source_id = owner[0] if owner and owner[0] else actor
                            source_name = (owner[1] if owner and owner[1] else
                                           self.entity_names.get(source_id))
                            is_self = source_id == self.own_entity
                            if source_name and (source_name in self.party_names or
                                                not self.party_names and is_self):
                                if target in self.boss_entities and target != self.current_boss:
                                    if self.current_boss is not None:
                                        self.emit(("dps_reset",))
                                    self.current_boss = target
                                self.emit(("dps_damage", source_name, amount))
                    elif op == OP_ZONE:
                        zone = parse_zone(payload)
                        if zone:
                            if zone[0] != self.zone_id:
                                if self.zone_id is not None:
                                    self.emit(("dps_reset",))
                                self.boss_entities.clear()
                                self.current_boss = None
                                self.own_entity = None
                            self.zone_id = zone[0]
                            self.emit(("zone",) + zone)
                    elif op == OP_LOCAL_POSITION:
                        position = parse_local_position(payload, self.own_entity)
                        if position:
                            self.emit(("position",) + position)
                    elif op in (OP_EFFECT, OP_EFFECT_RENEWED):
                        position = parse_effect_position(payload, self.own_entity,
                                                         op == OP_EFFECT_RENEWED)
                        if position:
                            self.emit(("position",) + position)
                    elif op == OP_NPC_SPAWN:
                        owner = parse_summon_owner(payload)
                        if owner and owner[1]:
                            self.summon_owners[owner[0]] = (owner[1], owner[2])
                        mob = parse_npc_spawn(payload)
                        if mob:
                            maximum = parse_spawn_max_hp(payload)
                            if maximum is not None and maximum >= 500_000:
                                self.boss_entities.add(mob[0])
                            self.emit(("mob_spawn",) + mob)
                    elif op == OP_NPC_REMOVE:
                        entity = parse_npc_remove(payload)
                        if entity is not None:
                            self.summon_owners.pop(entity, None)
                            self.boss_entities.discard(entity)
                            self.emit(("mob_remove", entity))
            except Exception as e:
                log("frame:", type(e).__name__)


# ---------------------------------------------------------------- capture

def run_sniffer(emit, stop_event=None):
    """Captures passively until stop_event is set; emits decoded events."""
    import psutil
    from scapy.all import IP, TCP, IPv6, AsyncSniffer, Raw, conf

    game_ports = set()

    def watch_game():
        while stop_event is None or not stop_event.is_set():
            try:
                pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "aion2.exe"}
                # Loopback peers are kept on purpose: ExitLag and similar boosters proxy
                # the game through 127.0.0.1, so that is where the plain stream shows up.
                ports = {c.laddr[1] for c in psutil.net_connections(kind="tcp")
                         if c.pid in pids and c.raddr and c.raddr[1] not in (80, 443)}
                game_ports.clear()
                game_ports.update(ports)
                emit(("conn", bool(ports)))
            except Exception as e:
                log("psutil:", e)
            time.sleep(2)

    threading.Thread(target=watch_game, daemon=True).start()
    decoder = Decoder(emit, INVENTORY_STATE_FILE)
    lock = threading.Lock()
    recent_packets = {}

    def on_packet(p):
        if TCP in p and p[TCP].dport in game_ports:
            src = p[IP].src if IP in p else p[IPv6].src if IPv6 in p else ""
            conn = (src, p[TCP].sport, p[TCP].dport)
            if int(p[TCP].flags) & 2:
                with lock:
                    decoder.streams.pop(conn, None)
                    decoder.ready = False
            if Raw not in p:
                return
            data = bytes(p[Raw].load)
            fingerprint = (src, p[TCP].sport, p[TCP].dport, p[TCP].seq,
                           hashlib.blake2b(data, digest_size=8).digest())
            with lock:
                now = time.monotonic()
                if fingerprint in recent_packets and now - recent_packets[fingerprint] < 10:
                    return  # mesma mensagem vista em duas interfaces (VPN/loopback)
                recent_packets[fingerprint] = now
                if len(recent_packets) > 4000:
                    recent_packets.clear()
                decoder.segment(conn, p[TCP].seq, data)

    # One sniffer per interface: a single multi-interface sniff polls them in turn on
    # Windows and falls far behind the game. The Npcap loopback adapter is included so
    # proxied (ExitLag) sessions are seen too.
    sniffers = []
    for iface in conf.ifaces.values():
        if not iface.ip:
            continue
        try:
            s = AsyncSniffer(iface=iface.network_name, filter="tcp and not port 443 and not port 80",
                             prn=on_packet, store=False)
            s.start()
            sniffers.append(s)
        except Exception as e:
            log("sniffer", iface.name, e)
    if not sniffers:
        raise RuntimeError("no interface could be opened")
    try:
        while stop_event is None or not stop_event.wait(0.5):
            if not any(s.running for s in sniffers):
                raise RuntimeError("captura Npcap parou em todas as interfaces")
    finally:
        for s in sniffers:
            if s.running:
                s.stop()


# ---------------------------------------------------------------- progress

class Tracker:
    """Per-pet level and souls toward the next level, mirroring the server.
    The server stops counting at max level, so overflow souls go to `extra`."""

    def __init__(self, path=STATE_FILE):
        self.path = path
        self.levels, self.points, self.extra = {}, {}, {}
        self.recent = []    # pet ids, most recent soul first
        self.synced = None  # time of the last full state from the server
        self.load()

    def load(self):
        for path in (self.path, LEGACY_STATE_FILE):
            try:
                with open(path, encoding="utf-8") as f:
                    d = json.load(f)
                break
            except (OSError, ValueError):
                continue
        else:
            return
        ints = lambda m: {int(k): v for k, v in m.items()}
        self.levels, self.points = ints(d.get("levels", {})), ints(d.get("points", {}))
        self.extra = ints(d.get("extra", {}))
        self.recent = d.get("recent", [])
        self.synced = d.get("synced")

    def save(self):
        d = {"levels": self.levels, "points": self.points, "extra": self.extra,
             "recent": self.recent, "synced": self.synced}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, self.path)

    def on_event(self, ev):
        if ev[0] == "state":
            self.levels, self.points = dict(ev[1]), dict(ev[2])
            self.synced = time.strftime("%m/%d %H:%M")
        elif ev[0] == "gain":
            _, pid, amount = ev
            self.add_points(pid, amount)
            if pid in self.recent:
                self.recent.remove(pid)
            self.recent.insert(0, pid)
            del self.recent[30:]

    def add_points(self, pid, amount):
        level = self.levels.get(pid, 0)
        if level >= MAX_LEVEL:
            self.extra[pid] = self.extra.get(pid, 0) + amount
            return
        pts = self.points.get(pid, 0) + amount
        while level < MAX_LEVEL and pts >= REQ[level]:
            pts -= REQ[level]
            level += 1
        if level >= MAX_LEVEL and pts:
            self.extra[pid] = self.extra.get(pid, 0) + pts
            pts = 0
        self.levels[pid], self.points[pid] = level, pts
