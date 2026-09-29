# EDA trước khi tích hợp OSMnx

## 1. Quy mô dữ liệu
- Incidents: 9,000
- Fleet initial rows: 900
- Hospitals: 6
- Charging stations: 4
- Hospital-capacity rows: 51,840
- Tổng điểm cần có thể map sang road nodes: 9,910
- Số tọa độ duy nhất: 9,910

## 2. Coordinate QA
- Incidents: lat 10.677924 → 10.855636; lon 106.555227 → 106.802642
- Fleet: lat 10.730070 → 10.799818; lon 106.630138 → 106.719982
- Hospitals: lat 10.757879 → 10.803963; lon 106.659528 → 106.694203
- Chargers: lat 10.755000 → 10.797000; lon 106.650000 → 106.705000
- Missing/invalid coordinates: 0 trong bốn nhóm.

## 3. BBox tổng
- South: 10.677924
- North: 10.855636
- West: 106.555227
- East: 106.802642
- Kích thước xấp xỉ: 19.8 km Bắc–Nam × 27.1 km Đông–Tây.
- File `osmnx_bbox.json` có thêm margin 2 km để tải graph thử nghiệm.

## 4. Điểm cần lưu ý trước OSMnx
1. Không route trực tiếp 540.000 candidate rows. Tách leg để tránh tính lặp:
   - vehicle/fleet-origin → incident;
   - incident → hospital;
   - hospital → charger.
2. `incident → hospital` chỉ có 9.000 × 6 = 54.000 cặp trước filtering, không phải 540.000.
3. `hospital → charger` chỉ có 6 × 4 = 24 cặp.
4. Map toàn bộ coordinate sang `nearest_node` trước, lưu cache node ID.
5. Tải road graph một lần cho study area; không query mạng cho từng mission.
6. Giữ Haversine/synthetic travel time làm baseline để đối chiếu sau khi thay bằng network shortest path.
