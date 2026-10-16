
from transformers import AutoTokenizer
import pathlib, json
MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")
QWEN = str(next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*")))
t = AutoTokenizer.from_pretrained(QWEN, local_files_only=True)
def n(x): return len(t(x, add_special_tokens=False)["input_ids"])
pairs = [
 ("OCC/2 D=sym-v1@1 C=1efce9992ee2", "OCC/1"),
 ("M=m-201", "MSGID=m-201"),
 ("T=occ2-01", "TASK=occ2-01"),
 ("R=trace-occ2-01", "TRACE=trace-occ2-01"),
 ("A=daemon://coordinator", "SENDER=daemon://coordinator"),
 ("B=agent://worker-b", "RECIPIENT=agent://worker-b"),
 ("K=TASK", "KIND=TASK"),
 ("S=pending", "STATUS=pending"),
 ("Q=1", "SEQ=1"),
 ("F1=implement the OCC-2 dialect", "F1=implement the OCC-2 dialect"),
 ("F1c=0.900", "FC1=0.900"),
 ("F1e=1,2", "FE1=E1,E2"),
 ("E1=src:crates/x.rs", "E1=src:crates/x.rs"),
 ("E1l=88-120", "EL1=88-120"),
 ("E1v=1", "EV1=pass"),
 ("G1=reports/x.md", "ART1=reports/x.md"),
 ("V=claim text here", "V=claim text here"),
 ("Vs=pass", "VS=pass"),
 ("D1=t39", "DEP1=t39"),
 ("N=survey the sites", "NEXT=survey the sites"),
 ("C=2026-10-16T09:02:00Z", "TS=2026-10-16T09:02:00Z"),
 ("X=2026-10-17T00:00:00Z", "EXP=2026-10-17T00:00:00Z"),
]
tot_s = tot_o = 0
for s,o in pairs:
    ns, no = n(s), n(o)
    tot_s += ns; tot_o += no
    print(f"{ns:3d} vs {no:3d}  {s!r}  vs  {o!r}")
print("TOTAL", tot_s, "vs", tot_o)
uuid = "01a142c2-5529-7649-a43d-d5f420e3389f"
print("one uuid:", n(uuid), "tokens,", len(uuid), "chars")
scaf = "[from sibling:worker-b]\nAgent-to-agent message received.\nSource: agent_message\nFrom: worker-b, active 01a142c2-5529-7649-a43d-d5f420e3389f, session 01a144bc-008c-75fd-b0e7-90ffd4e67f9b\nTo: coordinator, active 01a142c2-5529-7649-a43d-d5f420e3389f, session 01a144bc-008c-75fd-b0e7-90ffd4e67f9b\nMessage id: agentmsg_3f2a9c1e-8b4d-4e7a-9c5f-1d2e3f4a5b6c\n\n"
print("scaffold:", n(scaf), "tokens,", len(scaf), "chars")
cb = open(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_analysis/codebook.txt", encoding="utf-8").read()
print("codebook:", n(cb), "tokens,", len(cb), "chars")
