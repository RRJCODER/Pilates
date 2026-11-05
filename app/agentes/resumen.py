"""Resumen de conversaciones largas (RF-MEM-003, diseño §6.2)."""

from __future__ import annotations

from app.agentes.claude import BETA_FALLBACK, config_modelo, get_cliente, texto_final
from app.db.sesion import sesion
from app.servicios import conversaciones

PROMPT = ("Resume esta conversación de WhatsApp entre un estudio de pilates y una persona, "
          "para que el asistente pueda continuarla sin leerla entera. Incluye: peticiones y "
          "su resultado, cosas pendientes, preferencias expresadas y el tono de la persona. "
          "Máximo 150 palabras, en español, en viñetas. No incluyas datos de salud ni de pago "
          "más allá de lo imprescindible.")


def resumir_conversacion(conversacion_id: int) -> bool:
    with sesion() as s:
        mensajes = conversaciones.pendientes_de_resumir(s, conversacion_id)
        if not mensajes:
            return False
        previo = conversaciones.resumen_previo(s, conversacion_id)
        transcripcion = "\n".join(
            f"{'Persona' if m.rol == 'user' else 'Estudio'}: {m.contenido}" for m in mensajes)
        ids = [m.id for m in mensajes]
    if previo:
        transcripcion = f"Resumen anterior:\n{previo}\n\nContinuación:\n{transcripcion}"
    modelo, _ = config_modelo("subagente")
    msg = get_cliente().beta.messages.create(
        model=modelo, max_tokens=1500, system=PROMPT,
        messages=[{"role": "user", "content": transcripcion}],
        output_config={"effort": "low"},
        fallbacks="default", betas=[BETA_FALLBACK],
    )
    if msg.stop_reason == "refusal":
        return False
    with sesion() as s:
        mensajes = conversaciones.pendientes_de_resumir(s, conversacion_id)
        mensajes = [m for m in mensajes if m.id in ids]
        if mensajes:
            conversaciones.guardar_resumen(s, conversacion_id, texto_final(msg), mensajes)
    return True
