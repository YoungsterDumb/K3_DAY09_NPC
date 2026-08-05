# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                                                                  |
| --------------- | -------------------------------------------------------------------------|
| Họ và tên       | Nguyễn Đức Đạt                                                            |
| MSSV            | 2A202601633                                                               |
| Khóa/Lớp        | K3 / D304                                                                 |
| Vai trò chính   | Multi-agent Pipeline Engineer (kiến trúc, rule engine, agent, verifier, vận hành model) |
| Ngày hoàn thành | 2026-08-05                                                                |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| --- | --- | --- | --- | --- |
| Data layer xác định (join CSV) | `src/data_layer.py` | 5 CSV Olist (`orders`, `order_items`, `order_payments`, `sellers`, `customers`) | `OlistDataset`, `OrderBundle` (dữ liệu đã join theo `order_id`) | Hoàn thành |
| Rule engine `EC_POLICY_V1` | `src/rules.py` | `OrderBundle` (order/items/payments) | `PolicyDecision` (primary_issue, root cause, refund, action, confidence) | Hoàn thành |
| Agent LLM (Order&Seller, Delivery, Payment, Policy) | `src/agents.py`, `src/llm_client.py` | Facts đã trích xuất từ `OrderBundle` | `AgentMessage` (handoff: summary/flag do LLM sinh) ghi vào trace | Hoàn thành |
| Verifier + Coordinator (orchestration, xuất file) | `src/agents.py::VerifierAgent`, `src/pipeline.py` | `PolicyDecision` + `OrderBundle` của cả 50 case | `output/EC_001.json`…`EC_050.json`, `logging/trace.jsonl` | Hoàn thành |
| Tài liệu kiến trúc & vận hành | `architecture.md`, `workflow.md`, `logging/metadata.json` | Toàn bộ pipeline đã chạy | Sơ đồ agent, quy trình làm bài, thông tin model/runtime | Hoàn thành |

Tôi là người trực tiếp thiết kế và cài đặt toàn bộ pipeline này trong một phiên làm việc (không có thành viên khác tham gia phần code), nên nhận ownership cho tất cả các module trên.

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả và bằng chứng |
| --- | --- | --- |
| Debug lỗi nộp bài trên cổng chấm điểm | `output.zip` (đóng gói kết quả) | Phát hiện zip thiếu tiền tố thư mục `output/` khiến cổng nộp bài từ chối; sửa lại cách đóng gói, nộp lại thành công, được chấm 93.5335 điểm (ảnh chụp màn hình cổng chấm điểm) |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| --- | --- | --- | --- |
| Cài rule engine và đối chiếu với toàn bộ 50 `claimed_order_id` trước khi viết agent | `src/rules.py`, `src/data_layer.py` | Cả 50 order đều tra cứu được, cả 6 nhánh `primary_issue` đều được kích hoạt (phân bố 8–9 case/nhánh) | Script kiểm tra độc lập chạy trực tiếp `rules.evaluate()` trên dữ liệu thô, in phân bố và danh sách case lỗi (0 lỗi) |
| Chạy multi-agent pipeline cho 50 case | `src/pipeline.py` | `output/EC_001.json`…`EC_050.json`, `logging/trace.jsonl` | `python3 src/pipeline.py` — log in ra `[OK] EC_xxx` cho từng case, tổng kết "50 ok, 0 failed" |
| Kiểm chứng độc lập sau khi chạy | script kiểm tra ngoài pipeline (không lưu thành file riêng) | 0 vi phạm giới hạn schema (5 entity/10 evidence/3 cause/3 party/5 action), 0 sai khác giữa `output/*.json` và `rules.evaluate()` tính lại từ đầu trên 50/50 case | So sánh từng trường `primary_issue` và `recommended_refund_brl` giữa file output và kết quả tính lại |

Output cụ thể mà phần việc của tôi tạo ra và đã được xác minh: `output.zip` (đúng 50 file `output/EC_001.json`…`output/EC_050.json`, không có file lạ) — đã nộp lên cổng chấm điểm của môn học và được chấm **93.5335 điểm** (lần chấm sau khi đã sửa lỗi đóng gói zip). Đây là kết quả chạy bằng model `qwen2.5:3b-instruct`. Sau đó tôi có đổi sang model `gpt-4o-mini` (mục 5) và sửa lại thứ tự `evidence_ids`; các thay đổi này đã được xác minh nội bộ (0/50 sai khác so với rule engine) nhưng **chưa có lần chấm điểm mới trên cổng nộp bài** tại thời điểm viết báo cáo này — tôi không khẳng định điểm số sẽ thay đổi.

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Bài toán yêu cầu, với mỗi trong 50 case khiếu nại, đối chiếu dữ liệu từ 5 bảng CSV của Olist để xác định: vấn đề chính (`primary_issue`), bên chịu trách nhiệm, bằng chứng (evidence ID) có thể truy vết ngược lại CSV, khoản hoàn tiền và hành động xử lý — theo đúng bảng quy tắc `EC_POLICY_V1` trong README, không được để hệ thống tự suy diễn hoặc tin hoàn toàn vào lời khiếu nại của khách hàng.

### Cách triển khai

Tôi tách kiến trúc thành hai lớp:

1. **Lớp quyết định (deterministic)** — `src/rules.py` cài lại chính xác bảng ưu tiên trong README (canceled → unavailable → late_delivery_seller → late_delivery_logistics → valid_split_payment → unsupported_late_claim), so sánh ngày tháng (`order_delivered_customer_date` so với `order_estimated_delivery_date`; `order_delivered_carrier_date` so với `shipping_limit_date` từng item) và đối chiếu tiền (dung sai 0.10 BRL) bằng code thuần, không qua LLM.
2. **Lớp suy luận ngôn ngữ (LLM)** — 4 agent (Order&Seller, Delivery, Payment, Policy) nhận facts đã được trích xuất sẵn từ data layer, gọi LLM để tóm tắt/diễn giải bằng ngôn ngữ tự nhiên (phục vụ audit trail trong `trace.jsonl`), không phải nguồn quyết định. Coordinator (`src/pipeline.py`) điều phối thứ tự gọi; Verifier Agent (thuần code) dựng lại object output, kiểm tra từng evidence ID có tồn tại thật trong CSV, kiểm tra giới hạn schema, trước khi ghi file — nếu có vấn đề thì case bị loại (`case_failed`), không ghi output sai.

Lý do tách hai lớp: policy là phép so sánh ngày/tiền chính xác — rule engine không sai, còn để LLM tự quyết định có rủi ro hallucination. Nguyên tắc "ưu tiên dữ liệu có thể kiểm chứng" trong README được tôi áp dụng cho cả nội bộ hệ thống, không chỉ cho input khách hàng.

### Input, output và contract

| Thành phần | Mô tả |
| --- | --- |
| Input | `input/EC_001.json`…`EC_050.json` (case_id, claimed_order_id, customer message) + 5 CSV trong `data/` |
| Output | `output/EC_001.json`…`EC_050.json` đúng schema mục 6 README (assessment, affected_entities, root_cause_analysis, evidence_ids, financial_resolution, resolution_actions) |
| Module phụ thuộc | `src/data_layer.py` (join CSV), `src/llm_client.py` (gọi model) |
| Module sử dụng output | Không có module downstream trong repo này — output là bàn giao cuối để nộp bài (`output.zip`) |
| Điều kiện lỗi cần xử lý | `claimed_order_id` không tồn tại trong `orders.csv` (raise lỗi, không ghi file sai); order không có item row (item_ids/seller_ids để rỗng, item_total/freight_total = 0.0 theo đúng README) |

### Cách xác minh

```bash
# Chạy 1 case để debug
python3 src/pipeline.py --case EC_001

# Chạy toàn bộ 50 case
python3 src/pipeline.py

# Đối chiếu output với rule engine tính độc lập (không qua agent/LLM)
python3 -c "
import sys, json, glob
sys.path.insert(0, 'src')
from data_layer import get_dataset
from rules import evaluate
ds = get_dataset()
mismatches = 0
for f in sorted(glob.glob('input/EC_*.json')):
    d = json.load(open(f))
    oid = d['customer_request']['claimed_order_id']
    bundle = ds.get_bundle(oid)
    dec = evaluate(bundle.order, bundle.items, bundle.payments)
    out = json.load(open(f\"output/{d['case_id']}.json\"))
    if out['assessment']['primary_issue'] != dec.primary_issue:
        mismatches += 1
print('total mismatches:', mismatches, '/ 50')
"
```

- **Kết quả mong đợi:** cả 50 case chạy xong không lỗi (`50 ok, 0 failed`); 0 sai khác giữa output và rule engine tính độc lập.
- **Kết quả thực tế:** đúng như mong đợi — `50 ok, 0 failed` (2 lần chạy, với `qwen2.5:3b-instruct` và với `gpt-4o-mini`); `total mismatches: 0 / 50` cả hai lần.
- **Artifact/log:** `output/EC_001.json`…`EC_050.json`, `logging/trace.jsonl` (log thật của lượt chạy mới nhất, không chứa API key).

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Chọn cách để hệ thống ra quyết định `primary_issue`/số tiền hoàn — để LLM tự đọc dữ liệu và quyết định toàn bộ, hay tách riêng một rule engine xác định.
- **Các phương án đã cân nhắc:**
  1. Để mỗi agent LLM tự đọc facts và tự kết luận `primary_issue`, root cause, số tiền hoàn — đơn giản hơn về code, nhưng LLM (dù nhỏ hay lớn) có rủi ro đọc nhầm ngày tháng hoặc cộng sai số tiền, nhất là khi phải so sánh nhiều mốc thời gian (estimated/carrier/customer date) và nhiều dòng payment.
  2. Tách rule engine thuần Python (`src/rules.py`) làm nguồn quyết định, LLM chỉ dùng để tóm tắt/diễn giải cho audit trail; Verifier Agent tái xác minh độc lập từ dữ liệu thô trước khi ghi file.
- **Phương án đã chọn:** Phương án 2.
- **Lý do:** Policy `EC_POLICY_V1` là các phép so sánh chính xác đến giây/xu — đúng loại bài toán rule engine không sai. README cũng nhấn mạnh "ưu tiên dữ liệu có thể kiểm chứng thay vì tin hoàn toàn vào lời khiếu nại" — tôi áp dụng nguyên tắc này cho cả LLM nội bộ, không chỉ cho khách hàng.
- **Bằng chứng quyết định phù hợp:** Chạy đối chiếu độc lập `output/*.json` so với `rules.evaluate()` tính lại từ dữ liệu thô cho toàn bộ 50 case — kết quả khớp 100% (0 mismatch), cả ở lượt chạy với `qwen2.5:3b-instruct` lẫn lượt chạy sau khi đổi sang `gpt-4o-mini`, chứng minh việc đổi model không ảnh hưởng độ chính xác của quyết định nhờ kiến trúc tách lớp này.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Cổng nộp bài báo lỗi "ZIP phải chứa đúng output/EC_001.json đến output/EC_050.json" khi tôi nộp `output.zip` lần đầu.
- **Lệnh hoặc bước tái hiện:** `cd output && zip ../output.zip EC_*.json` rồi nộp file `output.zip` lên cổng chấm điểm.
- **Nguyên nhân gốc:** Lệnh zip được chạy từ bên trong thư mục `output/`, nên các entry lưu trong file zip chỉ có tên `EC_001.json` (không có tiền tố thư mục `output/`), trong khi cổng chấm điểm yêu cầu entry phải là `output/EC_001.json`.
- **Cách xử lý:** Chạy lại lệnh zip từ thư mục gốc repo: `zip output.zip output/EC_*.json`, giữ nguyên tiền tố `output/` cho cả 50 entry. (Trong lúc debug cũng phát hiện và dọn một thư mục lồng `output/output/` phát sinh ngoài ý muốn, đã gộp file về đúng vị trí `output/` trước khi đóng gói lại.)
- **Cách xác minh sau khi sửa:** `unzip -l output.zip` — xác nhận đủ 50 entry, mỗi entry có tên dạng `output/EC_0xx.json`; nộp lại trên cổng chấm điểm và được xác nhận "Đã chấm điểm — 93.5335 điểm" (không còn báo lỗi).
- **Điều học được:** Với các cổng chấm điểm tự động, cấu trúc đường dẫn bên trong file nén cũng là một phần của "đúng định dạng nộp bài" — không chỉ nội dung file JSON bên trong đúng schema là đủ, cần kiểm tra bằng `unzip -l` trước khi nộp thay vì chỉ tin vào việc "đã zip xong".

## 7. Hiểu biết về luồng end-to-end

Ghi chú: 5 câu hỏi mẫu gốc của template này (Crossref, vector index, freshness monitoring, baseline/corrupted/repaired) thuộc một bài lab khác (RAG/vector search), không áp dụng cho bài K3 Day 09 (multi-agent dispute resolution trên dữ liệu Olist). Tôi thay bằng 5 câu hỏi tương ứng đúng với luồng thực tế của bài này và tự trả lời:

1. Dữ liệu đi từ 9 CSV Olist đến `output/EC_xxx.json` như thế nào?
2. Bộ 50 case (`input/EC_001.json`…`EC_050.json`) và "ground truth" của bài này là gì, dùng để đo độ chính xác ra sao?
3. Ngoài kiểm tra schema, hệ thống còn quality check nào khác, ở đâu trong pipeline?
4. Vì sao rule engine tính quyết định và Verifier tái xác minh phải cùng dùng một nguồn dữ liệu thô (5 CSV), không phải dùng lại facts do LLM tóm tắt?
5. Một case được xem là xử lý thành công dựa trên artifact và tiêu chí nào?

**Câu trả lời:**

1. `src/data_layer.py` đọc 5 CSV (`orders`, `order_items`, `order_payments`, `sellers`, `customers`) một lần khi khởi động, join theo `order_id`/`seller_id` thành `OrderBundle`. Coordinator (`src/pipeline.py`) đọc `claimed_order_id` từ mỗi file input, lấy `OrderBundle` tương ứng, đưa qua 3 agent domain (Order&Seller, Delivery, Payment) để lấy facts + tóm tắt LLM, rồi Policy Agent chạy `rules.evaluate()` để ra quyết định. Verifier Agent dựng object theo đúng schema, kiểm tra lại evidence, rồi ghi ra `output/EC_xxx.json`.
2. Bộ 50 case chính thức đóng vai trò "test set": mỗi case có một `claimed_order_id` trỏ tới dữ liệu thật trong Olist CSV. "Ground truth" ở đây không phải nhãn có sẵn, mà là kết quả tính lại trực tiếp từ `rules.evaluate()` trên chính dữ liệu thô — tôi dùng nó để đối chiếu ngược với output của pipeline (script ở mục 4), đo được 0/50 sai khác.
3. Verifier Agent (`src/agents.py::VerifierAgent.verify`) là quality check chính ngoài schema: nó tra cứu lại từng ID trong `evidence_ids` (order/item/payment/seller/policy) trực tiếp trong `OlistDataset` để đảm bảo không có evidence "ảo" (false positive) trước khi cho phép ghi file.
4. Nếu Verifier chỉ xác minh dựa trên facts do LLM tóm tắt (thay vì đọc lại CSV gốc), một lỗi hoặc hallucination của LLM ở bước tóm tắt có thể "lọt" qua bước xác minh vì cả hai cùng dựa trên cùng một nguồn sai. Dùng lại đúng 5 CSV gốc cho cả rule engine lẫn Verifier đảm bảo bước xác minh độc lập thật sự, không phụ thuộc vào LLM.
5. Một case được xem là xử lý thành công khi: (a) pipeline chạy xong không rơi vào nhánh `case_failed` trong `trace.jsonl`; (b) file `output/EC_xxx.json` tồn tại, đúng 7 khóa top-level và không vượt giới hạn số lượng (5 entity/10 evidence/3 cause/3 party/5 action); (c) `primary_issue` và `recommended_refund_brl` khớp với kết quả tính lại từ `rules.evaluate()`. Cả 50/50 case đạt cả 3 tiêu chí này ở cả hai lần chạy (model `qwen2.5:3b-instruct` và `gpt-4o-mini`).

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Nguyễn Đức Đạt
**Ngày xác nhận:** 2026-08-05
