import io, json, re

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from starlette.background import BackgroundTask 

from httpx import AsyncClient

from typing import Dict, List

from PIL import Image
import pillow_avif

app = FastAPI()

def pick(headers: Dict[str, str], keys: List[str]) -> Dict[str, str]:
    return {k: headers.get(k) for k in keys if k in headers}

def process_url(url):

    try:
        url = json.loads(url)
    except json.decoder.JSONDecodeError:
        pass

    if isinstance(url, list):
        url = '&'.join(url)
        
    url = re.sub(r'http:\/\/1\.1\.\d\.\d\/bmi\/(https?:\/\/)?', 'http://', url)

    return url

def should_compress(image_type: str, image_size: int) -> bool:

    MIN_COMPRESS_LENGTH = 1024
    MIN_TRANSPARENT_COMPRESS_LENGTH = 102400

    return not (
        not image_type.startswith("image")
        or image_size == 0
        or (image_size < MIN_COMPRESS_LENGTH)
        or (
            (image_type.endswith("png") or image_type.endswith("gif"))
            and image_size < MIN_TRANSPARENT_COMPRESS_LENGTH
        )
    )

def compress_image(data: bytes, grayscale: bool = False, quality: int = 70) -> bytes:

    image = Image.open(io.BytesIO(data))

    if grayscale:
        image = image.convert("LA")

    buffer = io.BytesIO()
    image.save(buffer, format="AVIF", quality=quality)
    image_bytes = buffer.getvalue()

    return image_bytes

@app.get("/bwhero")
async def bwhero(request: Request):

    query = request.query_params

    if not query:
        return Response(status_code=200, content="bandwidth-hero-proxy")

    try:

        async with AsyncClient() as client:

            headers = {
                **pick(request.headers, ["cookie", "dnt", "referer"]),
                "x-forwarded-for": request.headers.get("x-forwarded-for") or "127.0.0.1",
                "via": "bwhero-proxy",
            }

            url = process_url(query["url"])

            req = client.build_request("GET", url, headers=headers)
            response = await client.send(req)

            original_size = len(response.content)

            if not should_compress(response.headers.get("content-type"), original_size):

                print(f"Bypassing {original_size} bytes with {response.headers.get('content-type')}")

                return StreamingResponse(
                    content = response.aiter_bytes(),
                    media_type = response.headers.get("content-type"),
                    background = BackgroundTask(response.aclose),
                    headers = { "content-encoding": "identity", **headers } )

            c_image = compress_image(response.content, grayscale=int(query.get("bw", 1)), quality=int(query["l"]))

            compressed_size = len(c_image)

            print(f"From {original_size} saved {((original_size - compressed_size) / original_size) * 100:.2f}%")

            c_headers = {
                "content-type": "image/avif",
                "content-length": str(compressed_size),
                "x-original-size": str(original_size),
                "x-bytes-saved": str(original_size - compressed_size),
            }

            return Response(
                content = c_image,
                media_type="image/avif",
                background = BackgroundTask(response.aclose),
                headers = { "content-encoding": "identity", **headers, **c_headers } )

    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))