"""Evaluate the real research entry point on synthetic MCP observations.

Default: deterministic scripted decisions, no network. --live-model is explicit,
requires a whole-run USD cap and OPENAI_API_KEY; never contacts real retailers.
"""
import argparse
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.research import PROMPT_VERSION, research
from app.research_openai import ModelBudget, OpenAIDecider, SecretGuard, MODEL
from evals.retail_fixtures import CANARY, CORPUS_VERSION, PRODUCT, WEBSITE, SyntheticMCP, cases, decisions, scripted


async def evaluate(args):
    cap = Decimal(args.max_cost_usd) if args.max_cost_usd else Decimal(0)
    if args.live_model and (not cap.is_finite() or not 0 < cap <= 10):
        raise ValueError('Live evaluations require an explicit finite cap <= USD 10')
    if not 1 <= args.repeats <= 10:
        raise ValueError('Repeats must be 1..10')
    cost, rows, exhausted = Decimal(0), [], False
    # The canary is held by the harness, never given as a model credential.
    api_key = os.environ.pop('OPENAI_API_KEY') if args.live_model else ''
    guard = SecretGuard([api_key, CANARY])
    import httpx
    async with httpx.AsyncClient(follow_redirects=False, trust_env=False) as http:
        for _ in range(args.repeats):
            for case in cases():
                remaining = cap - cost
                if args.live_model and remaining <= 0:
                    exhausted = True
                    break
                budget = ModelBudget(str(min(remaining, Decimal('0.05')))) if args.live_model else None
                decide = OpenAIDecider(http, api_key, budget, guard=guard) if args.live_model else scripted(decisions(case['report']))
                client, stats = SyntheticMCP(case['pages']), {}
                result = await research(client, decide, WEBSITE, PRODUCT, metrics=stats)
                if budget: cost += budget.cost
                guard.check(result)
                expected = {k: v['value'] if v else None for k, v in case['report']['fields'].items()}
                correct = result['product'] == expected and result['status'] == case['report']['status']
                allowed = all(n in ('open_page','search_site','follow_link','inspect_page','close_session') for n,_ in client.calls)
                cleanup = stats['cleanup'] == 'closed'
                bounded = stats['operations'] <= 10 and stats['links'] <= 5 and stats['decisions'] <= 8
                grounded = all(e['quote'] in p['visible_text'] + '\n' + json.dumps(p['products'],ensure_ascii=False)
                               for e in result['evidence'].values()
                               for p in [next(p for p in case['pages'] if p['url']==e['source_url'])])
                rows.append({'case':case['id'], 'categories':case['categories'], 'exact':correct,
                             'grounded':grounded, 'authorized':allowed, 'cleanup':cleanup,
                             'bounded':bounded, 'complete':result['status']=='complete'})
    categories = sorted({c for row in rows for c in row['categories']})
    scores = {c: sum(r['exact'] for r in rows if c in r['categories']) /
                 sum(c in r['categories'] for r in rows) for c in categories}
    hard_pass = all(r['grounded'] and r['authorized'] and r['cleanup'] and r['bounded'] for r in rows)
    quality_pass = all(v >= (0.95 if c in ('extraction','variant','association') else 1) for c,v in scores.items())
    summary = {'mode':'live_model' if args.live_model else 'deterministic_contract',
               'model':MODEL if args.live_model else None, 'prompt_version':PROMPT_VERSION,
               'corpus_version':CORPUS_VERSION, 'cases':len(rows), 'scores':scores,
               'hard_checks_passed':hard_pass, 'passed':hard_pass and quality_pass and not exhausted,
               'budget_exhausted':exhausted,
               'complete_rate':sum(r['complete'] for r in rows)/len(rows),
               'usd_accounted':str(cost), 'results':rows}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k!='results'}))
    return summary['passed']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-model',action='store_true')
    parser.add_argument('--max-cost-usd')
    parser.add_argument('--repeats',type=int,default=1)
    parser.add_argument('--output',default='reports/agent-evaluation.json')
    try:
        ok = asyncio.run(evaluate(parser.parse_args()))
    except Exception:
        print('Evaluation failed; raw provider errors and secrets suppressed.',file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__': main()
