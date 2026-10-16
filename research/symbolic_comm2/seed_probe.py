
import sys, pathlib, json, statistics
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import evolve2
for seed in (20261018, 20261019, 20261020):
    card, _hist = evolve2.run(generations=8, log_path=HERE / ("evolution2-log-seed%d.jsonl" % seed), seed=seed)
    items = evolve2.load_split("evolve")
    toks = evolve2.count_tokens([card.encode(i["message"]) for i in items])
    print("seed %d -> %s  mean %.2f tok" % (seed, card.card_id, statistics.mean(toks)), flush=True)
