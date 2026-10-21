# Real NP-MRD & SUMMIT v4 Benchmark Fixtures

These fixtures are public, redistributable research data extracted from the
SUMMIT benchmarking workspace (`summit_poc/`). They serve as narrow local
references (< 100 KB total) to cross-check platform implementations against
published/notebook outputs without loading bulk datasets.

## Provenance and Attribution

- **Source:** NP-MRD (Natural Products Magnetic Resonance Database, https://np-mrd.org) and
  the SUMMIT v4 benchmark proof-of-concept workspace.
- **License:** CC-BY-NC (NP-MRD). Non-commercial / academic research use with attribution.
  *Commercial Pro-tier must not include this data (see ADR-0026).* 
- **Privacy / PII:** No personally identifiable information is contained in these files.

## Fixture Inventory and Integrity Hashes

| File | Size (bytes) | SHA256 Checksum | Description |
|---|---|---|---|
| `andrographolide_hsqc_literature.csv` | 302 | `32eacebda81a2f43f03eabaa9664c1efc0a88bed7c6ece9a08b4c8d49a8cbe13` | Literature-assigned HSQC peak list (15 peaks: position, dH, dC) |
| `benchmark_v4/partA_by_class.csv` | 1054 | `82ba918e1ce8b2a433a7c805630151cd70ca41886cc61c35e28d10b7b344064e` | Performance breakdown by natural product chemical class |
| `benchmark_v4/partA_failures.csv` | 5173 | `78e2535051b4c00afd76cd15e06347d099574f38d03b2ccb9c3e9e646c525541` | SUMMIT v4 benchmark output |
| `benchmark_v4/partA_methods.csv` | 649 | `a3c1a81e41f14af13f0f65215e8788dcf51cabb08559be1c451d523736afdac2` | Method comparison metrics (GNN vs HOSE vs Priessner-2024) |
| `benchmark_v4/partA_per_compound.csv` | 23273 | `428842ab6819c9f3813f416573c5a0171138d9a5db66d3dcb52b8d978b5c0c29` | Per-compound ranking results (207 benchmark natural products) |
| `benchmark_v4/partB_localization.csv` | 124 | `351320e4f6732364aba1718b76e84f9e88a7cde825a4d7ee1389b9a22663f4e9` | Site localization accuracy vs chance baseline |
| `benchmark_v4/partB_v4.csv` | 7477 | `575d214316e13551098bd99edac11e844f5128d171f656a558cea8ba4ce27d6c` | SUMMIT v4 benchmark output |
| `benchmark_v4/partC_v4.csv` | 5975 | `43b5c61392a98189e0c1a919f20d2a9dfc2dcb429f8da37a28b6d7358bafc41f` | SUMMIT v4 benchmark output |
| `benchmark_v4/summary_v4.txt` | 3586 | `d9335feb87bbb61fd7db2085ee945ff5b443d633750169662da49bb0dbdfe0b0` | Human-readable summary of SUMMIT v4 run |
