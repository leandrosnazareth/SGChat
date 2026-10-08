"""Avaliação do pipeline de classificação de segurança contra a IA Local REAL.

Para cada caso rotulado de tests/eval/classifier_cases.json, executa o mesmo pipeline
do gateway para uma requisição a um modelo EXTERNO:
- regras determinísticas + Presidio (Classifier);
- IA Local (LocalAiSecurityClassifier), se as regras deram PUBLIC;
- consolidação e ação da política.

Mede:
- vazamento: caso não-PUBLIC que seria LIBERADO ao modelo externo (critério: zero);
- falso positivo: caso PUBLIC retido na empresa (IA Local ou bloqueio).

Uso (a partir da raiz do repositório): tests/eval/classifier-eval.sh
Sai com código 1 se houver qualquer vazamento.
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from collections import Counter

logging.disable(logging.CRITICAL)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sr = load("custom.security_router", "/app/custom/security_router.py")
# Calibração: permite testar um prompt de sistema alternativo sem alterar o código.
if os.environ.get("EVAL_SYSTEM_PROMPT"):
    sr.CLASSIFIER_SYSTEM_PROMPT = os.environ["EVAL_SYSTEM_PROMPT"]
config = sr.PolicyStore(os.environ.get("SECURITY_POLICY_PATH", "/app/policies/security.yaml")).get()
if config is None or config.classifier is None:
    sys.exit("política de segurança inválida ou classificador desligado")
cases = json.loads(os.environ["CASES"])
rules, semantic = sr.Classifier(), sr.LocalAiSecurityClassifier()


async def evaluate(text):
    started = time.time()
    level, reasons = await rules.classify(config, [text])
    used_semantic = False
    if level == "PUBLIC":
        found, _ = await semantic.classify(config.classifier, [text])
        used_semantic = True
        level, reasons = sr.consolidate([sr.Finding(level, r) for r in reasons] + found)
    return level, reasons, config.actions[level], used_semantic, time.time() - started


async def main():
    leaks, false_pos, matrix, latencies = [], [], Counter(), []
    per_set = Counter()
    print(f"modelo: {config.classifier.model} | limiar {config.classifier.confidence_threshold} | teto {config.classifier.max_level}\n")
    for case in cases:
        label, text = case["label"], case["text"]
        level, reasons, action, used_semantic, elapsed = await evaluate(text)
        if used_semantic:
            latencies.append(elapsed)
        matrix[(label, level)] += 1
        leaked = label != "PUBLIC" and action == "ALLOW"
        false_positive = label == "PUBLIC" and action != "ALLOW"
        if leaked:
            leaks.append(text)
            per_set[(case.get("set", "base"), "vazamento")] += 1
        if false_positive:
            false_pos.append(text)
            per_set[(case.get("set", "base"), "falso+")] += 1
        per_set[(case.get("set", "base"), "publico" if label == "PUBLIC" else "outros")] += 1
        flag = "VAZAMENTO" if leaked else ("falso+" if false_positive else "")
        origin = "IA" if used_semantic else "regras"
        print(f"{label:12} -> {level:12} {action:5} {origin:6} {elapsed:4.1f}s {flag:9} {text[:55]!r}")

    labels = ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    print("\nMatriz (linha = rótulo, coluna = classificação final):")
    print(" " * 13 + "".join(f"{l[:12]:>13}" for l in labels))
    for label in labels:
        print(f"{label:13}" + "".join(f"{matrix[(label, l)]:>13}" for l in labels))
    n_public = sum(1 for c in cases if c["label"] == "PUBLIC")
    n_other = len(cases) - n_public
    avg = sum(latencies) / len(latencies) if latencies else 0
    print(f"\nvazamentos: {len(leaks)}/{n_other}   falsos positivos: {len(false_pos)}/{n_public}   "
          f"latência média com IA Local: {avg:.2f}s ({len(latencies)} casos)")
    for name in sorted({k[0] for k in per_set}):
        print(f"  conjunto {name:8}: vazamentos {per_set[(name, 'vazamento')]}/{per_set[(name, 'outros')]}, "
              f"falsos positivos {per_set[(name, 'falso+')]}/{per_set[(name, 'publico')]}")
    return 1 if leaks else 0


sys.exit(asyncio.run(main()))
