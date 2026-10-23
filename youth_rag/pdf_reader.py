"""PDF 텍스트 추출을 별도 프로세스에서 실행해 복잡한 PDF의 무한 지연을 막는다."""
import io
import multiprocessing
from pypdf import PdfReader


def _extract(data, max_pages, connection):
    try:
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > max_pages:
            connection.send({"error": "PDF 페이지 수가 자동 수집 한도를 초과했습니다."})
            return
        pages = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append({"page": i, "text": text})
        connection.send({"pages": pages})
    except Exception:
        connection.send({"error": "PDF 본문을 추출할 수 없습니다."})
    finally:
        connection.close()


def extract_pdf(data, timeout=5, max_pages=80):
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_extract, args=(data, max_pages, sender), daemon=True)
    try:
        process.start()
        sender.close()
        if not receiver.poll(timeout):
            raise ValueError("PDF 본문 추출이 제한 시간을 초과했습니다.")
        result = receiver.recv()
        if "error" in result:
            raise ValueError(result["error"])
        return result["pages"]
    except EOFError:
        raise ValueError("PDF 본문 추출을 완료하지 못했습니다.") from None
    finally:
        receiver.close()
        sender.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=1)
