# PostGIS Geospatial Query Optimization & Query Plans

This document captures query plan profiles (`EXPLAIN (ANALYZE, BUFFERS)`) demonstrating the performance difference between naive un-indexed geographical scans and PostGIS **GiST (Generalized Search Tree)** spatial index lookups.

---

## 1. Candidate Nearest-Store Query

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT 
    store_id, 
    name, 
    ST_Distance(location, ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography) AS distance_meters
FROM stores
WHERE ST_DWithin(location, ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography, 5000)
ORDER BY location <-> ST_SetSRID(ST_MakePoint(77.6412, 12.9719), 4326)::geography
LIMIT 5;
```

---

## 2. Before: Sequential Scan (Without GiST Index)

```
                                                     QUERY PLAN
---------------------------------------------------------------------------------------------------------------------
 Limit  (cost=125.50..125.51 rows=5 width=158) (actual time=14.821..14.823 rows=5 loops=1)
   Buffers: shared hit=84 read=12
   ->  Sort  (cost=125.50..128.00 rows=1000 width=158) (actual time=14.819..14.820 rows=5 loops=1)
         Sort Key: ((location <-> '0101000020E610000028...'::geography))
         Sort Method: top-N heapsort  Memory: 26kB
         ->  Seq Scan on stores  (cost=0.00..102.00 rows=1000 width=158) (actual time=0.045..14.210 rows=8 loops=1)
               Filter: st_dwithin(location, '0101000020E610000028...'::geography, '5000'::double precision)
               Rows Removed by Filter: 992
               Buffers: shared hit=84 read=12
 Planning Time: 0.182 ms
 Execution Time: 14.862 ms
```

> [!WARNING]
> **Observation**: Without a spatial index, PostgreSQL must evaluate `ST_DWithin` on every row via a Sequential Scan ($O(N)$), incurring high CPU cost and elevated execution time (14.86 ms).

---

## 3. After: GiST Spatial Index Scan (`idx_stores_location_gist`)

```
                                                     QUERY PLAN
---------------------------------------------------------------------------------------------------------------------
 Limit  (cost=0.28..12.35 rows=5 width=158) (actual time=0.112..0.125 rows=5 loops=1)
   Buffers: shared hit=4
   ->  Index Scan using idx_stores_location_gist on stores (cost=0.28..48.50 rows=20 width=158) (actual time=0.110..0.122 rows=5 loops=1)
         Order By: (location <-> '0101000020E610000028...'::geography)
         Filter: st_dwithin(location, '0101000020E610000028...'::geography, '5000'::double precision)
         Buffers: shared hit=4
 Planning Time: 0.210 ms
 Execution Time: 0.148 ms
```

> [!TIP]
> **Performance Gain**:
> - Execution Time dropped from **14.86 ms $\rightarrow$ 0.148 ms** ($100\times$ speedup).
> - Buffer reads dropped from **96 pages $\rightarrow$ 4 pages**.
> - Uses KNN index operator (`<->`) to instantly retrieve nearest neighbors via R-tree traversal without sorting full table scans.
