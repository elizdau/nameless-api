from flask import Flask, request, jsonify
from datetime import datetime
import uuid

app = Flask(__name__)

# In-memory storage for now
memory_store = {}

@app.route("/memories", methods=["POST"])
def add_memory():
    data = request.json
    memory_id = str(uuid.uuid4())
    memory = {
        "id": memory_id,
        "timestamp": datetime.utcnow().isoformat(),
        "type": data.get("type", "unspecified"),
        "content": data.get("content", "")
    }
    memory_store[memory_id] = memory
    return jsonify(memory), 201

@app.route("/memories", methods=["GET"])
def get_all_memories():
    return jsonify(list(memory_store.values())), 200

@app.route("/memories/<memory_id>", methods=["GET"])
def get_memory(memory_id):
    memory = memory_store.get(memory_id)
    if memory:
        return jsonify(memory), 200
    else:
        return jsonify({"error": "Memory not found"}), 404

@app.route("/memories/<memory_id>", methods=["DELETE"])
def delete_memory(memory_id):
    if memory_id in memory_store:
        del memory_store[memory_id]
        return jsonify({"message": "Memory deleted"}), 200
    else:
        return jsonify({"error": "Memory not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
