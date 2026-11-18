"""Supplied subject matter, for reference only. No prose is placed on the editable artboard."""

TITLES = {
    "general": "Untitled design",
    "architecture": "Mutual AI Resilience (MAR)",
    "hypothesis": "Why I Propose This System",
}
BRIEFS = {
    "general": (
        "Start on a blank page. Choose a drawing tool, then drag on the artboard. "
        "Double-click a text layer to edit it. Use the project record to describe the "
        "creative choices you actually made and the sources you used."
    ),
    "architecture": (
        "SUPPLIED BRIEF · WRITING PROMPTS\n\n"
        "Keep the Italy and Albania lanes independently controlled. Consider showing postal/customs "
        "systems, label data and electronic customs records, while keeping the central supervisory "
        "AI coordinator distinct from national systems and from the human decision gate.\n\n"
        "Five-step planning prompt: capture → national validation → coordination → human analyst "
        "review → authorized decision and review trail. Decide what the coordinator receives, how "
        "analysts check a flag, who approves a response, and what the system must not do.\n\n"
        "Possible safeguards: minimum necessary sharing, role separation, audit, review and "
        "correction. State that this is a proposal without any claimed endorsement, deployment or "
        "proven efficacy. Six months and €98,000–180,000 are illustrative, not a price quote."
    ),
    "hypothesis": (
        "SUPPLIED BRIEF · UNPROVEN SCENARIO\n\n"
        "Nine separate illustrative stages to assess: (1) identity/property theft, (2) fencing, "
        "(3) conversion to financial value, (4) precious metals, (5) a shipment to Albania, "
        "(6) possible investment fraud, (7) possible financial layering, "
        "(8) possible proceeds returning to intermediaries, "
        "(9) a possible link to financing human trafficking. No stage or link is asserted as "
        "established in a real case.\n\n"
        "Give proposed mechanisms and independently verifiable evidence different visual roles. "
        "Distinguish shipment, money transfer and speculation. Ask what records, corroboration "
        "and alternative explanations would change your assessment. What would disprove this "
        "theory? A destination, nationality, barcode or metals declaration alone proves no crime."
    ),
}


def scaffold_lines(state):
    """Nonprinting guides only; selecting a layout never makes/export layers."""
    choice = state["scaffold"]
    w, h = state["paper"]["w"], state["paper"]["h"]
    if choice == "Bilateral swimlane":
        return ([("x", w*.50), ("y", h*.18), ("y", h*.79)], "Two national lanes / shared review")
    if choice in ("Annotated network", "Branched evidence graph"):
        return ([("x", w*.31), ("x", w*.69), ("y", h*.34), ("y", h*.65)], "Notes / connections / notes")
    if choice in ("Vertical process", "Annotated timeline"):
        return ([("x", w*.22), ("y", h*.22), ("y", h*.40), ("y", h*.58), ("y", h*.76)], "Sequence / annotation")
    if choice == "Vertical editorial":
        return ([("x", w*.10), ("x", w*.64), ("y", h*.24), ("y", h*.72)], "Narrative / evidence / open questions")
    return [], ""

