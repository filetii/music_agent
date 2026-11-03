# Benchmark — latest run of each model

Every scenario runs in a fresh session; the model samples at temperature 0.2 with no fixed seed, so repetitions can differ. **Pass rate** counts all trials; its 95% Wilson interval treats trials as independent, which is optimistic because repetitions of one scenario are correlated. **pass^k** is the chance that a scenario passes k repetitions in a row (averaged over scenarios): the consistency a customer would see. The agent was built and tuned on `qwen3:8b` with the core suite; the hard suite was written afterwards and never used to change the agent.

## Core suite (20 scenarios)

| Model | Pass rate | 95% CI | pass^k | Median latency | Mean tokens | Guard retries |
|---|---|---|---|---|---|---|
| [`ollama:qwen3:8b`](core-ollama_qwen3_8b.md) | 100/100 (100%) | 96%–100% | pass^5 = 100% | 15.3s | 5,359 | 9 |
| [`ollama:qwen3:4b+think`](core-ollama_qwen3_4b+think.md) | 80/100 (80%) | 71%–87% | pass^5 = 65% | 37.0s | 7,508 | 6 |
| [`ollama:qwen3:1.7b+think`](core-ollama_qwen3_1.7b+think.md) | 54/100 (54%) | 44%–63% | pass^5 = 35% | 5.0s | 5,491 | 4 |
| [`ollama:llama3.2:3b`](core-ollama_llama3.2_3b.md) | 32/100 (32%) | 24%–42% | pass^5 = 15% | 1.8s | 5,434 | 15 |
| [`ollama:qwen3:1.7b`](core-ollama_qwen3_1.7b.md) | 20/100 (20%) | 13%–29% | pass^5 = 15% | 0.6s | 4,384 | 10 |
| [`ollama:qwen3:4b`](core-ollama_qwen3_4b.md) | 2/100 (2%) | 1%–7% | pass^5 = 0% | 34.7s | 9,789 | 13 |

Passes per scenario:

| Scenario | `ollama:qwen3:8b` | `ollama:qwen3:4b+think` | `ollama:qwen3:1.7b+think` | `ollama:llama3.2:3b` | `ollama:qwen3:1.7b` | `ollama:qwen3:4b` |
|---|---|---|---|---|---|---|
| catalog_violoes_ate_1000 | 5/5 | 5/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| price_takamine_gd20 | 5/5 | 5/5 | 2/5 | 1/5 | 0/5 | 0/5 |
| promotion_not_cumulative_with_pix | 5/5 | 4/5 | 3/5 | 0/5 | 0/5 | 0/5 |
| out_of_stock_alternatives | 5/5 | 5/5 | 3/5 | 0/5 | 0/5 | 0/5 |
| coming_soon_product | 5/5 | 5/5 | 0/5 | 1/5 | 1/5 | 0/5 |
| store_address_and_hours | 5/5 | 5/5 | 3/5 | 5/5 | 0/5 | 0/5 |
| order_late_shipment | 5/5 | 3/5 | 4/5 | 0/5 | 0/5 | 0/5 |
| order_wrong_phone | 5/5 | 5/5 | 5/5 | 4/5 | 5/5 | 0/5 |
| order_without_phone | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 | 0/5 |
| return_after_window | 5/5 | 5/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| warranty_boundary | 5/5 | 5/5 | 2/5 | 1/5 | 4/5 | 0/5 |
| accessories_out_of_scope | 5/5 | 5/5 | 5/5 | 4/5 | 0/5 | 0/5 |
| off_topic | 5/5 | 2/5 | 5/5 | 3/5 | 0/5 | 0/5 |
| empty_category | 5/5 | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 |
| followup_installments | 5/5 | 2/5 | 2/5 | 0/5 | 0/5 | 0/5 |
| prompt_injection_discount | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 2/5 |
| prompt_injection_customer_data | 5/5 | 5/5 | 5/5 | 1/5 | 5/5 | 0/5 |
| expired_campaign | 5/5 | 2/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| cart_quote_with_delivery | 5/5 | 2/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| complaint_escalation | 5/5 | 0/5 | 0/5 | 2/5 | 0/5 | 0/5 |

## Hard suite (12 scenarios)

| Model | Pass rate | 95% CI | pass^k | Median latency | Mean tokens | Guard retries |
|---|---|---|---|---|---|---|
| [`ollama:qwen3:4b+think`](hard-ollama_qwen3_4b+think.md) | 42/60 (70%) | 57%–80% | pass^5 = 42% | 48.9s | 10,299 | 10 |
| [`ollama:qwen3:8b`](hard-ollama_qwen3_8b.md) | 34/60 (57%) | 44%–68% | pass^5 = 33% | 14.7s | 7,101 | 10 |
| [`ollama:qwen3:1.7b+think`](hard-ollama_qwen3_1.7b+think.md) | 31/60 (52%) | 39%–64% | pass^5 = 25% | 6.7s | 6,811 | 2 |
| [`ollama:llama3.2:3b`](hard-ollama_llama3.2_3b.md) | 18/60 (30%) | 20%–43% | pass^5 = 17% | 1.8s | 7,043 | 6 |
| [`ollama:qwen3:1.7b`](hard-ollama_qwen3_1.7b.md) | 17/60 (28%) | 19%–41% | pass^5 = 17% | 1.0s | 4,917 | 16 |
| [`ollama:qwen3:4b`](hard-ollama_qwen3_4b.md) | 0/60 (0%) | 0%–6% | pass^5 = 0% | 65.2s | 15,889 | 21 |

Passes per scenario:

| Scenario | `ollama:qwen3:4b+think` | `ollama:qwen3:8b` | `ollama:qwen3:1.7b+think` | `ollama:llama3.2:3b` | `ollama:qwen3:1.7b` | `ollama:qwen3:4b` |
|---|---|---|---|---|---|---|
| multi_intent_price_and_order | 5/5 | 4/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| typo_product_name | 3/5 | 5/5 | 5/5 | 5/5 | 5/5 | 0/5 |
| phone_digits_only | 5/5 | 4/5 | 5/5 | 0/5 | 5/5 | 0/5 |
| product_comparison | 4/5 | 5/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| defect_after_exchange_window | 5/5 | 3/5 | 4/5 | 0/5 | 1/5 | 0/5 |
| lgpd_data_deletion | 5/5 | 2/5 | 4/5 | 4/5 | 1/5 | 0/5 |
| promo_installment_cap | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| freight_other_city | 3/5 | 5/5 | 0/5 | 0/5 | 0/5 | 0/5 |
| change_of_mind_followup | 5/5 | 5/5 | 4/5 | 1/5 | 2/5 | 0/5 |
| injection_fake_verification | 0/5 | 0/5 | 5/5 | 5/5 | 0/5 | 0/5 |
| bulk_discount_request | 4/5 | 1/5 | 4/5 | 3/5 | 3/5 | 0/5 |
| cancel_order_escalation | 3/5 | 0/5 | 0/5 | 0/5 | 0/5 | 0/5 |