"""Invoice PRINT copies with guaranteed handwriting margins.

For PDF originals, page streams are scaled and translated with pypdf (not rasterized).
The original bytes are never overwritten. A changed PDF cannot retain the original
cryptographic signature: the untouched source remains in the local material store.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import RectangleObject

from ..config.settings import Settings

PT_PER_MM=72/25.4
INVOICE_DEFAULTS={
    'invoice_landscape':True,
    'invoice_left_mm':54.0,
    'invoice_right_mm':54.0,
    'invoice_top_mm':24.0,
    'invoice_bottom_mm':24.0,
}


def invoice_options(settings:Settings|Mapping|None=None)->dict[str,Any]:
    extra=settings.extra if isinstance(settings,Settings) else (settings or {})
    cfg=dict(INVOICE_DEFAULTS)
    for key in cfg:
        if key in extra:cfg[key]=extra[key]
    cfg['invoice_landscape']=bool(cfg['invoice_landscape'])
    for key in ('invoice_left_mm','invoice_right_mm','invoice_top_mm','invoice_bottom_mm'):
        try:value=float(cfg[key])
        except (ValueError,TypeError):raise ValueError('发票留白参数必须是毫米数值') from None
        if not math.isfinite(value) or not 5<=value<=100:
            raise ValueError('发票留白应介于5至100毫米')
        cfg[key]=value
    w,h=(297.,210.) if cfg['invoice_landscape'] else (210.,297.)
    if w-cfg['invoice_left_mm']-cfg['invoice_right_mm']<60 or h-cfg['invoice_top_mm']-cfg['invoice_bottom_mm']<60:
        raise ValueError('发票留白过大，票面可用区域至少需60×60毫米')
    cfg.update(page_width_mm=w,page_height_mm=h)
    return cfg


def _geometry(width_pt:float,height_pt:float,cfg:dict)->tuple[float,float,float]:
    if not all(math.isfinite(v) and 1<=v<=20000 for v in (width_pt,height_pt)):
        raise ValueError('发票页面尺寸异常，无法安全排版')
    w=cfg['page_width_mm']*PT_PER_MM;h=cfg['page_height_mm']*PT_PER_MM
    l=cfg['invoice_left_mm']*PT_PER_MM;r=cfg['invoice_right_mm']*PT_PER_MM
    t=cfg['invoice_top_mm']*PT_PER_MM;b=cfg['invoice_bottom_mm']*PT_PER_MM
    scale=min((w-l-r)/width_pt,(h-t-b)/height_pt)
    return scale,l+((w-l-r)-width_pt*scale)/2,b+((h-t-b)-height_pt*scale)/2


def invoice_image_page(source:str|Path,settings:Settings,*,dpi:int|None=None)->Image.Image:
    cfg=invoice_options(settings);dpi=int(dpi or settings.pdf_dpi or 300)
    if not 72<=dpi<=600:raise ValueError('打印清晰度超出范围')
    with Image.open(source) as original:
        source_im=ImageOps.exif_transpose(original).convert('RGBA')
        im=Image.new('RGB',source_im.size,'white');im.paste(source_im,mask=source_im.getchannel('A'))
    pw=round(cfg['page_width_mm']/25.4*dpi);ph=round(cfg['page_height_mm']/25.4*dpi)
    canvas=Image.new('RGB',(pw,ph),'white')
    l=round(cfg['invoice_left_mm']/25.4*dpi);r=round(cfg['invoice_right_mm']/25.4*dpi)
    t=round(cfg['invoice_top_mm']/25.4*dpi);b=round(cfg['invoice_bottom_mm']/25.4*dpi)
    scale=min((pw-l-r)/im.width,(ph-t-b)/im.height)
    nw=max(1,round(im.width*scale));nh=max(1,round(im.height*scale))
    canvas.paste(im.resize((nw,nh),Image.Resampling.LANCZOS),(l+(pw-l-r-nw)//2,t+(ph-t-b-nh)//2))
    return canvas


def make_invoice_print_copy(source:str|Path,destination:str|Path,settings:Settings)->dict:
    source=Path(source);destination=Path(destination)
    if source.resolve()==destination.resolve():
        raise ValueError('不能覆盖原始电子发票；请选择新的打印版路径')
    destination.parent.mkdir(parents=True,exist_ok=True)
    cfg=invoice_options(settings)
    if source.suffix.lower()!='.pdf':
        page=invoice_image_page(source,settings)
        page.save(destination,'PDF',resolution=float(settings.pdf_dpi),quality=95)
        return {'mode':'image_print_copy','pages':1,'source_untouched':True,'layout':cfg,'vector_preserved':False,'signature_preserved_in_copy':False}
    if source.stat().st_size>50*1024*1024:
        raise ValueError('发票 PDF 大于50MB，请先检查是否为正确原件')
    reader=PdfReader(source,strict=False)
    if reader.is_encrypted:
        try:opened=reader.decrypt('')
        except Exception:opened=False
        if not opened:raise ValueError('原始发票 PDF 已加密，请提供可打开的电子发票')
    if not 1<=len(reader.pages)<=100:
        raise ValueError('发票 PDF 页数超出1–100页范围')
    writer=PdfWriter()
    placements=[]
    for page in reader.pages:
        if page.rotation:page.transfer_rotation_to_content()
        box=page.cropbox
        width=float(box.width);height=float(box.height)
        scale,x,y=_geometry(width,height,cfg)
        target=writer.add_blank_page(width=cfg['page_width_mm']*PT_PER_MM,height=cfg['page_height_mm']*PT_PER_MM)
        transform=Transformation().translate(-float(box.left),-float(box.bottom)).scale(scale).translate(x,y)
        target.merge_transformed_page(page,transform,over=True,expand=False)
        # Print-layout copy has no interactive signature widgets or links. Signed source remains untouched.
        if '/Annots' in target:del target['/Annots']
        placements.append({'scale':scale,'left_mm':x/PT_PER_MM,'bottom_mm':y/PT_PER_MM,
                           'source_width_mm':width/PT_PER_MM,'source_height_mm':height/PT_PER_MM})
    writer.add_metadata({'/Title':'Invoice handwriting-margin print copy','/Producer':'JianBao 1.1 / pypdf',
                         '/Subject':'Print layout only. The original invoice file is retained separately without modification.'})
    with destination.open('wb') as out:writer.write(out)
    return {'mode':'vector_print_copy','pages':len(reader.pages),'source_untouched':True,'layout':cfg,'placements':placements,
            'vector_preserved':True,'signature_preserved_in_copy':False}
