from backend.graph.graph_connection import connect_graph

db, graph = connect_graph()

result = graph.ro_query(
    "MATCH (n) RETURN count(n) AS node_count"
)
print("Connected to deployed FalkorDB; nodes:", result.result_set[0][0])

db.close()
