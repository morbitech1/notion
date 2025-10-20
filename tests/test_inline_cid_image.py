from .fixtures import *  # noqa


def make_inline_image_email():
    msg = EmailMessage()
    msg['Subject'] = 'Inline Image Test'
    msg['From'] = 'alice@example.com'
    msg['To'] = 'bob@example.com'
    # multipart/related with html referencing cid
    msg.set_content('Plain fall back')
    html = '<html><body><p>Hello</p><img src="cid:img1" alt="Logo" /></body></html>'
    msg.add_alternative(html, subtype='html')
    # Add inline image part with Content-ID
    img_bytes = b'\x89PNG\r\ninlinefake'  # small fake content
    img_part = EmailMessage()
    img_part.add_header('Content-Type', 'image/png')
    img_part.add_header('Content-ID', '<img1>')
    img_part.add_header('Content-Transfer-Encoding', 'base64')
    import base64
    img_part.set_payload(base64.b64encode(img_bytes).decode())
    # Force related structure: EmailMessage does not build related automatically; we append
    msg.make_mixed()  # ensure we can attach binary part
    msg.attach(img_part)
    return msg


async def test_inline_cid_image_block():
    msg = make_inline_image_email()
    blocks = await nu.build_email_content_blocks(msg)
    # Expect an image block present
    types = [b.get('type') for b in blocks if isinstance(b, dict)]
    assert 'image' in types, f'Types missing image: {types}'
    # Find image block and ensure URL is either data: or external http (future S3)
    imgs = [b for b in blocks if b.get('type') == 'image']
    assert imgs, 'No image blocks produced'
    url = imgs[0]['image']['external']['url']
    assert url.startswith('data:') or url.startswith('http'), f'Unexpected URL {url}'
