"""molt-forge — The Capability Bazaar.

The Forge Master is not a member of a fixed mesh. It is a market maker:
it discovers third-party molt-reactor capabilities, probes them black-box,
negotiates signed contracts over the gossip layer, composes them into a
pipeline it did not (and may not) write, proves the composition safe
*before* first execution, and hot-swaps live vendors under continuous
proof — with every decision executed by its own signed molt-reactor rules
and replayable from its genesis event.

Primitive layer (envelopes, ledgers, identity, anchor) is molt-mesh:
the vendors are molt-reactor programs and speak the same wire protocol.
"""
import sys
from pathlib import Path

_MOLT_MESH = Path(__file__).resolve().parents[2] / "molt-mesh"
if str(_MOLT_MESH) not in sys.path:
    sys.path.insert(0, str(_MOLT_MESH))

PROTOCOL = "forge/1"
MESH_ID = "molt-forge"
EPOCH = 1