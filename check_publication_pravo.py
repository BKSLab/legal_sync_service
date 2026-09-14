"""Manual checker for publication.pravo.gov.ru API.

Usage examples:
    python check_publication_pravo.py
    python check_publication_pravo.py --page-size 1
    python check_publication_pravo.py --download-first --save-dir fixtures/publication_pravo
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

DEFAULT_BASE_URL = "http://publication.pravo.gov.ru"
DOCUMENT_FIELDS = ("eoNumber", "name", "complexName", "documentDate", "id")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check publication.pravo.gov.ru /api/Documents response.",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--raw-url", default=None)
    parser.add_argument("--period-type", default="daily")
    parser.add_argument("--block", default="president")
    parser.add_argument("--page-size", default="30")
    parser.add_argument("--period-id", default=None)
    parser.add_argument("--publish-date-from", default=None)
    parser.add_argument("--publish-date-to", default=None)
    parser.add_argument("--search", default=None)
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        help="Additional raw query param in key=value format. Can be passed multiple times.",
    )
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--read-timeout", type=float, default=30.0)
    parser.add_argument("--try-root", action="store_true")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--statistics", choices=["daily", "weekly", "monthly"], default=None)
    parser.add_argument("--document-types", action="store_true")
    parser.add_argument("--document", default=None, help="Fetch /api/Document by eoNumber.")
    parser.add_argument("--document-text", action="store_true", help="Fetch /api/DocumentText by eoNumber.")
    parser.add_argument("--extract-pdf-text", default=None, help="Extract text from a local PDF file.")
    parser.add_argument("--extract-pages", type=int, default=3, help="How many PDF pages to inspect.")
    parser.add_argument("--download-first", action="store_true")
    parser.add_argument("--try-file-candidates", action="store_true")
    parser.add_argument("--save-dir", default=None)
    return parser.parse_args()


def find_candidate_documents(payload: Any) -> list[dict[str, Any]]:
    """Finds the most likely list of document objects in an unknown JSON shape."""

    candidates: list[list[dict[str, Any]]] = []

    def walk(value: Any) -> None:
        if isinstance(value, list):
            dict_items = [item for item in value if isinstance(item, dict)]
            if dict_items:
                candidates.append(dict_items)
            for item in value:
                walk(item)
            return

        if isinstance(value, dict):
            for item in value.values():
                walk(item)

    walk(payload)

    def score(items: list[dict[str, Any]]) -> int:
        return sum(
            1
            for item in items[:10]
            for field in DOCUMENT_FIELDS
            if field in item
        )

    if not candidates:
        return []

    return max(candidates, key=score)


def print_json_shape(payload: Any) -> None:
    print(f"JSON root type: {type(payload).__name__}")
    if isinstance(payload, dict):
        print(f"Top-level keys: {', '.join(payload.keys())}")
        for field in ("itemsTotalCount", "itemsPerPage", "currentPage", "pagesTotalCount"):
            if field in payload:
                print(f"{field}: {payload.get(field)}")
    elif isinstance(payload, list):
        print(f"Root list length: {len(payload)}")


def print_documents(documents: list[dict[str, Any]], limit: int = 5) -> None:
    print(f"Candidate documents found: {len(documents)}")
    for index, document in enumerate(documents[:limit], start=1):
        print(f"\nDocument #{index}")
        for field in DOCUMENT_FIELDS:
            print(f"  {field}: {document.get(field)}")


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def save_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def safe_label(label: str) -> str:
    return label.replace(" ", "_").replace("/", "_")


def print_text_preview(text: str, limit: int = 2000) -> None:
    normalized = " ".join(text.split())
    if not normalized:
        print("Text preview: <empty>", flush=True)
        return
    print(f"Text preview: {normalized[:limit]}", flush=True)


def diagnose_network(url: str, connect_timeout: float) -> None:
    parsed_url = urlparse(url)
    host = parsed_url.hostname
    port = parsed_url.port or (443 if parsed_url.scheme == "https" else 80)
    if not host:
        print("Cannot diagnose network: host is empty.", flush=True)
        return

    print(f"\nNetwork diagnostics for {host}:{port}", flush=True)
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        print(f"DNS error: {type(error).__name__}: {error}", flush=True)
        return

    seen: set[tuple[str, int]] = set()
    for family, socktype, proto, _canonname, sockaddr in addresses:
        ip = sockaddr[0]
        key = (ip, port)
        if key in seen:
            continue
        seen.add(key)

        family_name = "IPv6" if family == socket.AF_INET6 else "IPv4"
        print(f"TCP connect {family_name} {ip}:{port} ... ", end="", flush=True)
        started_at = time.monotonic()
        sock = socket.socket(family, socktype, proto)
        sock.settimeout(connect_timeout)
        try:
            sock.connect(sockaddr)
        except OSError as error:
            elapsed = time.monotonic() - started_at
            print(f"FAILED after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)
        else:
            elapsed = time.monotonic() - started_at
            print(f"OK after {elapsed:.2f}s", flush=True)
        finally:
            sock.close()


def main() -> None:
    args = parse_args()
    if args.extract_pdf_text:
        inspect_pdf_text_layer(
            pdf_path=Path(args.extract_pdf_text),
            max_pages=args.extract_pages,
            save_dir=Path(args.save_dir) if args.save_dir else None,
        )
        return

    base_url = args.base_url.rstrip("/")
    params = {}
    if args.page_size:
        params["PageSize"] = args.page_size
    if args.period_type:
        params["PeriodType"] = args.period_type
    if args.block:
        params["Block"] = args.block
    if args.period_id:
        params["PeriodId"] = args.period_id
    if args.publish_date_from:
        params["PublishDateFrom"] = args.publish_date_from
    if args.publish_date_to:
        params["PublishDateTo"] = args.publish_date_to
    if args.search:
        params["Search"] = args.search
    for raw_param in args.param:
        if "=" not in raw_param:
            raise ValueError(f"Invalid --param value: {raw_param}. Expected key=value.")
        key, value = raw_param.split("=", 1)
        params[key] = value

    timeout = httpx.Timeout(
        connect=args.connect_timeout,
        read=args.read_timeout,
        write=10.0,
        pool=10.0,
        )

    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        target_url = args.raw_url or f"{base_url}/api/Documents"
        if args.diagnose:
            diagnose_network(target_url, connect_timeout=args.connect_timeout)

        if args.statistics:
            statistics_url = f"{base_url}/api/BlockStatistics/{args.statistics}"
            print(f"GET {statistics_url}", flush=True)
            started_at = time.monotonic()
            try:
                statistics_response = client.get(statistics_url)
            except httpx.HTTPError as error:
                elapsed = time.monotonic() - started_at
                print(
                    f"Statistics error after {elapsed:.2f}s: {type(error).__name__}: {error}",
                    flush=True,
                )
                return

            elapsed = time.monotonic() - started_at
            print(f"Status: {statistics_response.status_code}", flush=True)
            print(f"Content-Type: {statistics_response.headers.get('content-type')}", flush=True)
            print(f"Body bytes: {len(statistics_response.content)}", flush=True)
            print(f"Elapsed: {elapsed:.2f}s", flush=True)
            if statistics_response.is_error:
                print("\nStatistics error response body:", flush=True)
                print(statistics_response.text, flush=True)
                return
            for item in statistics_response.json():
                print(f"{item.get('block')}: {item.get('documentsCount')}", flush=True)
            return

        if args.document_types:
            document_types_url = f"{base_url}/api/DocumentTypes"
            document_types_params = {}
            if args.block:
                document_types_params["block"] = args.block
            print(f"GET {document_types_url}", flush=True)
            print(f"Params: {document_types_params}", flush=True)
            started_at = time.monotonic()
            try:
                document_types_response = client.get(
                    document_types_url,
                    params=document_types_params or None,
                )
            except httpx.HTTPError as error:
                elapsed = time.monotonic() - started_at
                print(
                    f"Document types error after {elapsed:.2f}s: {type(error).__name__}: {error}",
                    flush=True,
                )
                return

            elapsed = time.monotonic() - started_at
            print(f"Status: {document_types_response.status_code}", flush=True)
            print(f"Content-Type: {document_types_response.headers.get('content-type')}", flush=True)
            print(f"Body bytes: {len(document_types_response.content)}", flush=True)
            print(f"Elapsed: {elapsed:.2f}s", flush=True)
            if document_types_response.is_error:
                print("\nDocument types error response body:", flush=True)
                print(document_types_response.text, flush=True)
                return
            for item in document_types_response.json():
                print(f"{item.get('id')} | {item.get('name')} | weight={item.get('weight')}", flush=True)
            return

        if args.document:
            document = fetch_document_detail(
                client=client,
                base_url=base_url,
                eo_number=args.document,
            )
            if args.save_dir and document:
                save_dir = Path(args.save_dir)
                save_text(
                    save_dir / f"document_{args.document}.json",
                    json.dumps(document, ensure_ascii=False, indent=2),
                )
                print(f"Saved document detail: {save_dir / f'document_{args.document}.json'}")
            if args.document_text:
                fetch_document_text(
                    client=client,
                    base_url=base_url,
                    eo_number=args.document,
                    save_dir=Path(args.save_dir) if args.save_dir else None,
                )
            if args.try_file_candidates and document:
                try_file_candidates(
                    client=client,
                    base_url=base_url,
                    document=document,
                    save_dir=Path(args.save_dir) if args.save_dir else None,
                )
            return

        if args.try_root:
            root_url = f"{base_url}/"
            print(f"GET {root_url}", flush=True)
            started_at = time.monotonic()
            try:
                root_response = client.get(root_url)
                elapsed = time.monotonic() - started_at
                print(f"Root status: {root_response.status_code}", flush=True)
                print(f"Root content-type: {root_response.headers.get('content-type')}", flush=True)
                print(f"Root body bytes: {len(root_response.content)}", flush=True)
                print(f"Root elapsed: {elapsed:.2f}s", flush=True)
            except httpx.HTTPError as error:
                elapsed = time.monotonic() - started_at
                print(f"Root error after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)

        documents_url = target_url
        print(f"GET {documents_url}", flush=True)
        if args.raw_url:
            print("Params: <raw-url mode>", flush=True)
        else:
            print(f"Params: {params}", flush=True)
        started_at = time.monotonic()
        try:
            response = client.get(documents_url, params=None if args.raw_url else params)
        except httpx.HTTPError as error:
            elapsed = time.monotonic() - started_at
            print(f"Request error after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)
            return

        elapsed = time.monotonic() - started_at
        print(f"Status: {response.status_code}", flush=True)
        print(f"Content-Type: {response.headers.get('content-type')}", flush=True)
        print(f"Body bytes: {len(response.content)}", flush=True)
        print(f"Elapsed: {elapsed:.2f}s", flush=True)
        if response.is_error:
            print("\nError response body:", flush=True)
            print(response.text, flush=True)
            return

        payload = response.json()
        print_json_shape(payload)

        if args.save_dir:
            save_dir = Path(args.save_dir)
            save_text(
                save_dir / "documents_response.json",
                json.dumps(payload, ensure_ascii=False, indent=2),
            )
            print(f"Saved JSON: {save_dir / 'documents_response.json'}")

        documents = find_candidate_documents(payload)
        print_documents(documents)

        if not args.download_first:
            return

        first_eo_number = next(
            (
                str(document["eoNumber"])
                for document in documents
                if document.get("eoNumber")
            ),
            None,
        )
        if not first_eo_number:
            print("No eoNumber found, cannot check file download.")
            return

        first_document = next(
            (
                document
                for document in documents
                if str(document.get("eoNumber")) == first_eo_number
            ),
            {"eoNumber": first_eo_number},
        )
        detail = fetch_document_detail(
            client=client,
            base_url=base_url,
            eo_number=first_eo_number,
        )
        if detail:
            first_document.update(detail)
            if args.save_dir:
                save_dir = Path(args.save_dir)
                save_text(
                    save_dir / f"document_{first_eo_number}.json",
                    json.dumps(detail, ensure_ascii=False, indent=2),
                )
                print(f"Saved document detail: {save_dir / f'document_{first_eo_number}.json'}")

        if args.document_text:
            fetch_document_text(
                client=client,
                base_url=base_url,
                eo_number=first_eo_number,
                save_dir=Path(args.save_dir) if args.save_dir else None,
            )

        if args.try_file_candidates:
            try_file_candidates(
                client=client,
                base_url=base_url,
                document=first_document,
                save_dir=Path(args.save_dir) if args.save_dir else None,
            )
            return

        file_url = f"{base_url}/File/GetFile/{first_eo_number}"
        print(f"\nGET {file_url}", flush=True)
        started_at = time.monotonic()
        try:
            file_response = client.get(file_url)
        except httpx.HTTPError as error:
            elapsed = time.monotonic() - started_at
            print(f"File request error after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)
            return

        elapsed = time.monotonic() - started_at
        print(f"Status: {file_response.status_code}", flush=True)
        print(f"Content-Type: {file_response.headers.get('content-type')}", flush=True)
        print(f"Body bytes: {len(file_response.content)}", flush=True)
        print(f"Elapsed: {elapsed:.2f}s", flush=True)
        if file_response.is_error:
            print("\nFile error response body:", flush=True)
            print(file_response.text, flush=True)
            return

        if args.save_dir:
            save_dir = Path(args.save_dir)
            file_path = save_dir / f"{first_eo_number}.pdf"
            save_bytes(file_path, file_response.content)
            print(f"Saved file: {file_path}")


def fetch_document_detail(
    client: httpx.Client,
    base_url: str,
    eo_number: str,
) -> dict[str, Any]:
    document_url = f"{base_url}/api/Document"
    print(f"\nGET {document_url}", flush=True)
    print(f"Params: {{'eoNumber': '{eo_number}'}}", flush=True)
    started_at = time.monotonic()
    try:
        response = client.get(document_url, params={"eoNumber": eo_number})
    except httpx.HTTPError as error:
        elapsed = time.monotonic() - started_at
        print(f"Document detail error after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)
        return {}

    elapsed = time.monotonic() - started_at
    print(f"Status: {response.status_code}", flush=True)
    print(f"Content-Type: {response.headers.get('content-type')}", flush=True)
    print(f"Body bytes: {len(response.content)}", flush=True)
    print(f"Elapsed: {elapsed:.2f}s", flush=True)
    if response.is_error:
        print("\nDocument detail error response body:", flush=True)
        print(response.text, flush=True)
        return {}

    document = response.json()
    print("Document detail fields:", flush=True)
    for field in (
        "id",
        "eoNumber",
        "name",
        "complexName",
        "documentDate",
        "publishDateShort",
        "pagesCount",
        "pdfFileLength",
        "zipFileLength",
    ):
        print(f"  {field}: {document.get(field)}", flush=True)
    return document


def inspect_pdf_text_layer(pdf_path: Path, max_pages: int, save_dir: Path | None) -> None:
    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber is not installed.", flush=True)
        return

    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", flush=True)
        return

    extracted_parts: list[str] = []
    print(f"PDF: {pdf_path}", flush=True)
    print(f"PDF bytes: {pdf_path.stat().st_size}", flush=True)
    with pdfplumber.open(pdf_path) as pdf:
        print(f"PDF pages: {len(pdf.pages)}", flush=True)
        for page_number, page in enumerate(pdf.pages[:max_pages], start=1):
            text = page.extract_text() or ""
            extracted_parts.append(text)
            print(f"\nPage #{page_number}", flush=True)
            print(f"  text chars: {len(text)}", flush=True)
            print(f"  pdf chars: {len(page.chars)}", flush=True)
            print(f"  images: {len(page.images)}", flush=True)
            print_text_preview(text, limit=1000)

    extracted_text = "\n\n".join(part for part in extracted_parts if part.strip())
    if save_dir and extracted_text:
        text_path = save_dir / f"{pdf_path.stem}.txt"
        save_text(text_path, extracted_text)
        print(f"\nSaved extracted PDF text: {text_path}", flush=True)

    if not extracted_text:
        print("\nNo embedded text found in inspected pages. OCR is required for this PDF.", flush=True)


def fetch_document_text(
    client: httpx.Client,
    base_url: str,
    eo_number: str,
    save_dir: Path | None,
) -> None:
    text_url = f"{base_url}/api/DocumentText"
    print(f"\nGET {text_url}", flush=True)
    print(f"Params: {{'eonumber': '{eo_number}'}}", flush=True)
    started_at = time.monotonic()
    try:
        response = client.get(text_url, params={"eonumber": eo_number})
    except httpx.HTTPError as error:
        elapsed = time.monotonic() - started_at
        print(f"Document text error after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)
        return

    elapsed = time.monotonic() - started_at
    content_type = response.headers.get("content-type")
    print(f"Status: {response.status_code}", flush=True)
    print(f"Content-Type: {content_type}", flush=True)
    print(f"Body bytes: {len(response.content)}", flush=True)
    print(f"Elapsed: {elapsed:.2f}s", flush=True)
    if response.is_error:
        print("\nDocument text error response body:", flush=True)
        print(response.text, flush=True)
        return

    if content_type is not None and "json" in content_type.lower():
        payload = response.json()
        print_json_shape(payload)
        if save_dir:
            json_path = save_dir / f"document_text_{eo_number}.json"
            save_text(json_path, json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"Saved document text JSON: {json_path}", flush=True)
        if isinstance(payload, str):
            print_text_preview(payload)
        else:
            print_text_preview(json.dumps(payload, ensure_ascii=False))
        return

    text = response.text
    if save_dir:
        text_path = save_dir / f"document_text_{eo_number}.txt"
        save_text(text_path, text)
        print(f"Saved document text: {text_path}", flush=True)
    print_text_preview(text)


def try_file_candidates(
    client: httpx.Client,
    base_url: str,
    document: dict[str, Any],
    save_dir: Path | None,
) -> None:
    eo_number = str(document.get("eoNumber") or "")
    document_id = str(document.get("id") or "")
    candidates = [
        ("PDF query eoNumber", f"{base_url}/file/pdf?eoNumber={eo_number}"),
        ("DocumentText query eoNumber", f"{base_url}/api/DocumentText?eonumber={eo_number}"),
        ("File/GetFile eoNumber", f"{base_url}/File/GetFile/{eo_number}"),
        ("File/GetFile id", f"{base_url}/File/GetFile/{document_id}"),
        ("Document page", f"{base_url}/document/{eo_number}"),
        ("Document capitalized page", f"{base_url}/Document/{eo_number}"),
        ("File query eoNumber", f"{base_url}/File/GetFile?eoNumber={eo_number}"),
        ("File query id", f"{base_url}/File/GetFile?id={document_id}"),
    ]

    for label, url in candidates:
        print(f"\nGET {url} ({label})", flush=True)
        started_at = time.monotonic()
        try:
            response = client.get(url)
        except httpx.HTTPError as error:
            elapsed = time.monotonic() - started_at
            print(f"Error after {elapsed:.2f}s: {type(error).__name__}: {error}", flush=True)
            continue

        elapsed = time.monotonic() - started_at
        content_type = response.headers.get("content-type")
        print(f"Status: {response.status_code}", flush=True)
        print(f"Content-Type: {content_type}", flush=True)
        print(f"Body bytes: {len(response.content)}", flush=True)
        print(f"Elapsed: {elapsed:.2f}s", flush=True)
        print(f"Final URL: {response.url}", flush=True)

        is_pdf = response.content.startswith(b"%PDF") or (
            content_type is not None and "pdf" in content_type.lower()
        )
        if is_pdf and save_dir:
            file_path = save_dir / f"{eo_number}_{safe_label(label)}.pdf"
            save_bytes(file_path, response.content)
            print(f"Saved candidate PDF: {file_path}", flush=True)
        elif content_type is not None and "html" in content_type.lower():
            if save_dir:
                html_path = save_dir / f"{eo_number}_{safe_label(label)}.html"
                save_bytes(html_path, response.content)
                print(f"Saved candidate HTML: {html_path}", flush=True)
            print_interesting_html_links(response.text)
        elif content_type is not None and "json" in content_type.lower():
            if save_dir:
                json_path = save_dir / f"{eo_number}_{safe_label(label)}.json"
                save_text(json_path, json.dumps(response.json(), ensure_ascii=False, indent=2))
                print(f"Saved candidate JSON: {json_path}", flush=True)
            print_text_preview(json.dumps(response.json(), ensure_ascii=False))
        else:
            if save_dir:
                text_path = save_dir / f"{eo_number}_{safe_label(label)}.txt"
                save_text(text_path, response.text)
                print(f"Saved candidate text: {text_path}", flush=True)
            print_text_preview(response.text)


def print_interesting_html_links(html: str) -> None:
    soup = BeautifulSoup(html, "html.parser")
    interesting_markers = (
        "file",
        "pdf",
        "download",
        "document",
        "get",
        "api",
        "svg",
        "zip",
    )
    found: list[str] = []

    for tag in soup.find_all(True):
        for attr_name, attr_value in tag.attrs.items():
            values = attr_value if isinstance(attr_value, list) else [attr_value]
            for value in values:
                if not isinstance(value, str):
                    continue
                lowered = value.lower()
                if any(marker in lowered for marker in interesting_markers):
                    found.append(f"{tag.name}[{attr_name}]={value}")

    scripts_text = "\n".join(script.get_text("\n", strip=True) for script in soup.find_all("script"))
    for line in scripts_text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in interesting_markers):
            found.append(f"script: {line[:300]}")

    if not found:
        print("No interesting HTML links or script markers found.", flush=True)
        return

    print("Interesting HTML links/script markers:", flush=True)
    for item in found[:80]:
        print(f"  {item}", flush=True)
    if len(found) > 80:
        print(f"  ... {len(found) - 80} more items omitted", flush=True)


if __name__ == "__main__":
    main()
