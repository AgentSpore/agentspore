"""In-memory Docker daemon behind httpx.MockTransport, shared by the launcher tests."""

import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

DEP = "ab" * 16
NS = f"a4-{DEP}-1"
DIGEST = "c" * 64
IMAGE = f"127.0.0.1:5000/{NS}@sha256:{DIGEST}"


@dataclass
class FakeDaemon:
    image_info: dict = field(default_factory=dict)
    containers: dict = field(default_factory=dict)
    networks: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    image_list: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self.image_info = {
            "RepoDigests": [IMAGE],
            "Os": "linux",
            "Architecture": "amd64",
            "Config": {"Volumes": None},
            **self.image_info,
        }

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def bodies(self, method: str, path_re: str) -> list[dict]:
        return [b for m, p, b in self.calls if m == method and re.fullmatch(path_re, p)]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1.44")
        body: Any = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        name = path.rsplit("/", 1)[-1]
        if path == "/images/create":
            return httpx.Response(200, content=b'{"status":"Pull complete"}\n')
        if path == "/images/json":
            return httpx.Response(200, json=self.image_list)
        if request.method == "DELETE" and path.startswith("/images/"):
            return httpx.Response(200, json=[])
        if path.startswith("/images/") and path.endswith("/json"):
            return httpx.Response(200, json=self.image_info)
        if path == "/networks":
            return httpx.Response(
                200,
                json=[
                    {"Name": n, "IPAM": {"Config": [{"Subnet": v["sub"]}]}}
                    for n, v in self.networks.items()
                ],
            )
        if path == "/networks/create":
            subnet = body["IPAM"]["Config"][0]["Subnet"]
            self.networks[body["Name"]] = {
                "sub": subnet,
                "Labels": body["Labels"],
                "Containers": {},
            }
            return httpx.Response(201, json={"Id": "n1"})
        if path.startswith("/networks/"):
            return self._network(request.method, path, body)
        if path == "/containers/create":
            return self._create(request.url.params["name"], body)
        if path == "/containers/json":
            return httpx.Response(
                200,
                json=[
                    {"Id": c["Id"], "State": "created", "Labels": c["Config"]["Labels"]}
                    for c in self.containers.values()
                ],
            )
        if path.startswith("/containers/"):
            return self._container(request.method, path, name)
        return httpx.Response(404, json={"message": "no route"})

    def _network(self, method: str, path: str, body):
        parts = path.split("/")
        net = self.networks.get(parts[2])
        if net is None:
            return httpx.Response(404, json={"message": "no such network"})
        if method == "GET":
            return httpx.Response(
                200, json={"Labels": net["Labels"], "Containers": net["Containers"]}
            )
        if len(parts) == 3:
            del self.networks[parts[2]]
        elif parts[3] == "connect":
            net["Containers"][body["Container"]] = {"Name": body["Container"]}
        else:
            net["Containers"].pop(body["Container"], None)
        return httpx.Response(204 if len(parts) == 3 else 200)

    def _create(self, name: str, body: dict):
        if name in self.containers:
            return httpx.Response(409, json={"message": "conflict"})
        self.containers[name] = {
            "Id": f"id-{name}",
            "Config": {"Image": body["Image"], "Labels": body["Labels"]},
        }
        return httpx.Response(201, json={"Id": f"id-{name}"})

    def _container(self, method: str, path: str, name: str):
        ns = path.split("/")[2]
        cont = self.containers.get(ns)
        if cont is None:
            return httpx.Response(404, json={"message": "no such container"})
        if method == "DELETE":
            del self.containers[ns]
            return httpx.Response(204)
        if method == "GET":
            return httpx.Response(200, json=cont)
        return httpx.Response(204)
