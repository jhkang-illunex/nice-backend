1) CRI 신용등급은 노치를 제거하고 순서대로 단순하게 1~10까지 부여했습니다. 
2) Output 중 기업별 판매망 가중평균 CRI는 나 자신의 신용등급을 제외하고 나의 판매네트워크에 속한 기업들의 신용등급의 판매비중 가중값입니다. 
   - 소스코드와 아웃풋에는 구매망도 추가해놓았습니다. 





관심있는 결과는  -기업별 판매망/구매망 가중평균 CRI와 
전체 네트워크 지표인 -판매망/구매망 Network Risk Index: 이겠습니다. 
Input 데이터에서 매핑을 이용해서 계산값은 다시 등급처럼 표현할 수도 있겠습니다. 



누적 판매망 결과
--------------------------------------------------------------------------------
기업 | 전체누적비중 | 유효누적비중 | Coverage | 가중평균CRI | CRI Exposure
--------------------------------------------------------------------------------
 A |     0.883621 |     0.495690 | 0.560976 |    3.739130 |     1.853448
 B |     0.814655 |     0.814655 | 1.000000 |    3.439153 |     2.801724
 C |     0.000000 |     0.000000 |        - |           - |     0.000000
 D |     0.954741 |     0.631466 | 0.661400 |    4.436860 |     2.801724
 E |     0.000000 |     0.000000 |        - |           - |     0.000000





누적 구매망 결과
--------------------------------------------------------------------------------
기업 | 전체누적비중 | 유효누적비중 | Coverage | 가중평균CRI | CRI Exposure
--------------------------------------------------------------------------------
 A |     0.211204 |     0.038793 | 0.183673 |    3.000000 |     0.116378
 B |     0.727280 |     0.727280 | 1.000000 |    2.333370 |     1.697015
 C |     1.443882 |     0.581824 | 0.402958 |    2.333370 |     1.357612
 D |     0.416245 |     0.358832 | 0.862069 |    2.000000 |     0.717663
 E |     0.857498 |     0.823050 | 0.959827 |    2.738413 |     2.253850


네트워크 전체 결과
--------------------------------------------------------------------------------
판매망 Network Risk Index: 3.784242
판매망 Network Coverage  : 0.723983
구매망 Network Risk Index: 2.393419
구매망 Network Coverage  : 0.690818


Input 데이터와 소스코드는 아래에 첨부드립니다. 
드렸던 데이터에는 loop 경우가 없을 수 있어서 전에 논리적 경우의 수를 감안한 위의 샘플네트워크에 대해서 먼저 회신드립니다. 



from collections import defaultdict
# ============================================================
# 1. 샘플 데이터
# ============================================================

grades = {
    "A": "AA",
    "B": "NR",   # loop 안에 있는 NR 노드
    "C": "BBB",
    "D": "A",
    "E": "BB",
}

sales = {
    "A": 1000,
    "B": 800,
    "C": 500,
    "D": 600,
    "E": 400,
}

score = {
    "AAA": 1,
    "AA": 2,
    "A": 3,
    "BBB": 4,
    "BB": 5,
    "B": 6,
    "CCC": 7,
    "CC": 8,
    "C": 9,
    "D": 10,
}

# source -> target = source가 target에게 판매
# sell_share = source 매출 대비 target에게 판매한 비중
# buy_share  = target 매출 대비 source로부터 구매한 비중
edges = [
    ("A", "B", 0.300, 0.375),
    ("A", "D", 0.200, 0.333),
    ("D", "B", 0.300, 0.225),
    ("D", "E", 0.400, 0.600),
    ("B", "C", 0.500, 0.800),
    ("B", "A", 0.200, 0.160),
]


nodes = list(grades.keys())

# ============================================================
# 2. 판매망 / 구매망 그래프 만들기
# ============================================================

sell_graph = defaultdict(list)
buy_graph = defaultdict(list)

for source, target, sell_share, buy_share in edges:
    # 판매망: 판매기업 -> 구매기업
    sell_graph[source].append((target, sell_share))

    # 구매망: 구매기업 -> 공급기업
    buy_graph[target].append((source, buy_share))


# ============================================================
# 3. loop 포함 누적 거래망 계산
# ============================================================

def propagate(graph, epsilon=1e-8, max_iter=1000, lamb=1.0):
    """
    T = W + λW² + λ²W³ + ... 를 edge-list로 계산한다.

    1단계 직접 거래 W도 포함한다.
    다만 별도 '1단계 결과표'는 만들지 않는다.


    loop 예:
      A -> B -> A
      A -> D -> B -> A

    self-return 경로는 전파 계산에는 포함한다.
    단, 최종 CRI 계산에서는 자기 자신의 등급이 섞이지 않도록 제외한다.
    """

    current = defaultdict(float)


    # 1단계 직접 거래
    for src in graph:
        for dst, w in graph[src]:
            current[(src, dst)] += w

    total = defaultdict(float)

    for step in range(1, max_iter + 1):
        if sum(abs(v) for v in current.values()) < epsilon:
            return total, step - 1

        # 현재 단계 경로 누적
        for key, value in current.items():
            total[key] += value

        # 다음 단계 경로 계산
        nxt = defaultdict(float)


        for (src, mid), path_weight in current.items():
            for dst, edge_weight in graph.get(mid, []):
                nxt[(src, dst)] += path_weight * edge_weight * lamb


        current = nxt
    return total, max_iter


# ============================================================
# 4. 기업별 지표 계산
# ============================================================
def calc_company_index(total_paths):
    """
    기업별 지표 계산
    가중평균 CRI:
      Σ(누적거래비중 × 거래처 CRI점수) / Σ(유효 누적거래비중)

    CRI Exposure:
      Σ(누적거래비중 × 거래처 CRI점수)

    Coverage:
      유효 누적거래비중 / 전체 누적거래비중

    R/NR:
      전파에는 포함되지만 CRI 계산에서는 제외

    self-return:
      전파에는 포함되지만 최종 CRI 계산에서는 제외
    """

    result = {}


    for i in nodes:
        total_weight = 0.0
        valid_weight = 0.0
        weighted_score = 0.0

        for (src, dst), w in total_paths.items():
            if src != i:
                continue

            # 자기 자신으로 돌아온 경로는 최종 CRI 계산에서 제외
            if dst == i:
                continue

            total_weight += w
            grade = grades[dst]

            # AAA~D만 유효등급. R/NR은 제외.
            if grade in score:
                valid_weight += w
                weighted_score += w * score[grade]

        avg_cri = weighted_score / valid_weight if valid_weight > 0 else None
        coverage = valid_weight / total_weight if total_weight > 0 else None

        result[i] = {
            "total_weight": total_weight,
            "valid_weight": valid_weight,
            "coverage": coverage,
            "avg_cri": avg_cri,
            "exposure": weighted_score,
        }

    return result



# ============================================================
# 5. 네트워크 전체 Risk Index 계산
# ============================================================

def calc_network_index(company_result):
    """
    Network Risk Index
    = Σ 매출액_i × Exposure_i / Σ 매출액_i × 유효 누적거래비중_i
    전체 네트워크의 유효 거래망 기준 거래상대방 평균 CRI 수준.
    """
    numerator = 0.0
    denominator = 0.0
    coverage_denominator = 0.0


    for i, r in company_result.items():
        numerator += sales[i] * r["exposure"]
        denominator += sales[i] * r["valid_weight"]
        coverage_denominator += sales[i] * r["total_weight"]

    index = numerator / denominator if denominator > 0 else None
    coverage = denominator / coverage_denominator if coverage_denominator > 0 else None
    return index, coverage

# ============================================================
# 6. 실행
# ============================================================

sell_total, sell_steps = propagate(sell_graph)
buy_total, buy_steps = propagate(buy_graph)

sell_result = calc_company_index(sell_total)
buy_result = calc_company_index(buy_total)

sell_network_index, sell_network_coverage = calc_network_index(sell_result)
buy_network_index, buy_network_coverage = calc_network_index(buy_result)

# ============================================================
# 7. 출력
# ============================================================
def fmt(x):
    return "-" if x is None else f"{x:.6f}"

def print_company_result(title, result):
    print("\n" + title)
    print("-" * 80)
    print("기업 | 전체누적비중 | 유효누적비중 | Coverage | 가중평균CRI | CRI Exposure")
    print("-" * 80)

    for i in nodes:
        r = result[i]
        print(
            f"{i:>2} | "
            f"{fmt(r['total_weight']):>12} | "
            f"{fmt(r['valid_weight']):>12} | "
            f"{fmt(r['coverage']):>8} | "
            f"{fmt(r['avg_cri']):>11} | "
            f"{fmt(r['exposure']):>12}"
        )

print("누적 판매망 반복 단계:", sell_steps)
print("누적 구매망 반복 단계:", buy_steps)

print_company_result("누적 판매망 결과", sell_result)
print_company_result("누적 구매망 결과", buy_result)

print("\n네트워크 전체 결과")
print("-" * 80)
print(f"판매망 Network Risk Index: {sell_network_index:.6f}")
print(f"판매망 Network Coverage  : {sell_network_coverage:.6f}")
print(f"구매망 Network Risk Index: {buy_network_index:.6f}")
print(f"구매망 Network Coverage  : {buy_network_coverage:.6f}")

print("\nLoop 확인: 판매망 self-return 경로")
print("-" * 80)

for (src, dst), w in sell_total.items():
    if src == dst:
        print(f"{src} -> {dst}: {w:.6f}")

print("\nLoop 확인: 구매망 self-return 경로")
print("-" * 80)

for (src, dst), w in buy_total.items():
    if src == dst:
        print(f"{src} -> {dst}: {w:.6f}")

