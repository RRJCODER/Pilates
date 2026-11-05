"""Los cuatro subagentes especialistas y sus herramientas (diseño §5).

Cada fábrica recibe el contexto del turno (construido por el código) y devuelve
las herramientas ya "atadas" a ese remitente: el modelo nunca elige en nombre de
quién actúa (RNF-SEG-002).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from anthropic import beta_tool

from app.agentes.herramientas import fecha, fecha_hora, llamar, rango_dias, zona_estudio
from app.servicios import (
    bonos,
    clases,
    clientes,
    lista_espera,
    marketing,
    pagos,
    reservas,
)
from app.servicios.base import Contexto


def herramientas_reservas(ctx: Contexto) -> list[Any]:
    tz = zona_estudio(ctx)

    def c(herramienta: str, fn: Callable[..., Any], /, **kw: Any) -> str:
        return llamar(ctx, "reservas", herramienta, fn, **kw)

    @beta_tool
    def buscar_clases(desde: str, hasta: str | None = None, modalidad: str | None = None,
                      nivel: str | None = None, instructor: str | None = None,
                      solo_con_plazas: bool = False) -> str:
        """Busca clases programadas con sus plazas libres.

        Args:
            desde: Fecha 'AAAA-MM-DD' o fecha y hora local 'AAAA-MM-DDTHH:MM'.
            hasta: Fin del rango (incluido si es solo fecha). Por defecto, el mismo día.
            modalidad: 'reformer' o 'suelo'.
            nivel: 'inicial', 'intermedio' o 'avanzado'.
            instructor: Nombre (o parte) del instructor.
            solo_con_plazas: Devolver solo clases con plazas libres.
        """
        ini, fin = rango_dias(desde, hasta, tz)
        return c("buscar_clases", clases.buscar_clases, desde=ini, hasta=fin,
                 modalidad=modalidad, nivel=nivel, instructor=instructor,
                 solo_con_plazas=solo_con_plazas)

    @beta_tool
    def reservar_clase(clase_id: int) -> str:
        """Reserva una plaza en una clase para quien escribe y descuenta una sesión del bono.
        Si está llena devuelve 'clase_llena' con alternativas.

        Args:
            clase_id: Id de la clase (de buscar_clases).
        """
        return c("reservar_clase", reservas.reservar_clase, clase_id=clase_id)

    @beta_tool
    def reservar_recurrente(dia_semana: int, hora: str, semanas: int = 4,
                            modalidad: str | None = None) -> str:
        """Reserva la misma clase semanal varias semanas seguidas.

        Args:
            dia_semana: 0=lunes, 1=martes … 6=domingo.
            hora: Hora local 'HH:MM'.
            semanas: Número de semanas (1-12).
            modalidad: 'reformer' o 'suelo', si hay varias clases a esa hora.
        """
        return c("reservar_recurrente", reservas.reservar_recurrente, dia_semana=dia_semana,
                 hora=hora, semanas=semanas, modalidad=modalidad)

    @beta_tool
    def cancelar_reserva(reserva_id: int) -> str:
        """Cancela una reserva de quien escribe aplicando la política de cancelación.

        Args:
            reserva_id: Id de la reserva (de mis_reservas).
        """
        return c("cancelar_reserva", reservas.cancelar_reserva, reserva_id=reserva_id)

    @beta_tool
    def mis_reservas() -> str:
        """Lista las próximas reservas de quien escribe."""
        return c("mis_reservas", reservas.mis_reservas)

    @beta_tool
    def unirse_lista_espera(clase_id: int) -> str:
        """Apunta a quien escribe en la lista de espera de una clase llena.

        Args:
            clase_id: Id de la clase.
        """
        return c("unirse_lista_espera", lista_espera.unirse, clase_id=clase_id)

    @beta_tool
    def aceptar_plaza_lista_espera(clase_id: int) -> str:
        """Acepta la plaza que se le ha ofrecido desde la lista de espera.

        Args:
            clase_id: Id de la clase ofrecida.
        """
        return c("aceptar_plaza_lista_espera", reservas.aceptar_plaza_lista_espera,
                 clase_id=clase_id)

    @beta_tool
    def clases_del_instructor(desde: str, hasta: str | None = None) -> str:
        """(Staff) Clases del instructor que escribe (el admin ve todas).

        Args:
            desde: Fecha 'AAAA-MM-DD'.
            hasta: Fecha final incluida. Por defecto, el mismo día.
        """
        ini, fin = rango_dias(desde, hasta, tz)
        return c("clases_del_instructor", clases.clases_de_instructor, desde=ini, hasta=fin)

    @beta_tool
    def asistentes_clase(clase_id: int) -> str:
        """(Staff) Asistentes de una clase con sus lesiones o limitaciones.

        Args:
            clase_id: Id de la clase.
        """
        return c("asistentes_clase", clases.asistentes_clase, clase_id=clase_id)

    @beta_tool
    def marcar_asistencia(reserva_id: int, asistio: bool) -> str:
        """(Staff) Marca si un alumno asistió o faltó.

        Args:
            reserva_id: Id de la reserva (de asistentes_clase).
            asistio: true si vino, false si faltó.
        """
        return c("marcar_asistencia", clases.marcar_asistencia, reserva_id=reserva_id,
                 asistio=asistio)

    @beta_tool
    def crear_clase(tipo_clave: str, inicio: str, instructor: str,
                    aforo: int | None = None) -> str:
        """(Admin) Crea una clase nueva.

        Args:
            tipo_clave: Clave del tipo de clase (p. ej. 'reformer_inicial', 'suelo').
            inicio: Fecha y hora local 'AAAA-MM-DDTHH:MM'.
            instructor: Nombre del instructor.
            aforo: Aforo; por defecto el del tipo de clase.
        """
        return c("crear_clase", clases.crear_clase, tipo_clave=tipo_clave,
                 inicio=fecha_hora(inicio, tz), instructor=instructor, aforo=aforo)

    @beta_tool
    def modificar_clase(clase_id: int, inicio: str | None = None,
                        instructor: str | None = None, aforo: int | None = None) -> str:
        """(Admin) Cambia hora, instructor o aforo de una clase y avisa a los inscritos.

        Args:
            clase_id: Id de la clase.
            inicio: Nueva fecha y hora local 'AAAA-MM-DDTHH:MM'.
            instructor: Nuevo instructor.
            aforo: Nuevo aforo.
        """
        return c("modificar_clase", clases.modificar_clase, clase_id=clase_id,
                 inicio=fecha_hora(inicio, tz) if inicio else None, instructor=instructor,
                 aforo=aforo)

    @beta_tool
    def cancelar_clase(clase_id: int, motivo: str) -> str:
        """(Admin) Pide cancelar una clase. Crea una acción pendiente de confirmación.

        Args:
            clase_id: Id de la clase.
            motivo: Motivo que se comunicará a los inscritos.
        """
        return c("cancelar_clase", clases.cancelar_clase, clase_id=clase_id, motivo=motivo)

    return [buscar_clases, reservar_clase, reservar_recurrente, cancelar_reserva, mis_reservas,
            unirse_lista_espera, aceptar_plaza_lista_espera, clases_del_instructor,
            asistentes_clase, marcar_asistencia, crear_clase, modificar_clase, cancelar_clase]


def herramientas_clientes(ctx: Contexto) -> list[Any]:
    def c(herramienta: str, fn: Callable[..., Any], /, **kw: Any) -> str:
        return llamar(ctx, "clientes", herramienta, fn, **kw)

    @beta_tool
    def alta_cliente(nombre: str, acepta_privacidad: bool, email: str | None = None,
                     nivel: str | None = None, objetivos: str | None = None,
                     como_nos_conocio: str | None = None, consent_salud: bool = False,
                     consent_marketing: bool = False,
                     fecha_nacimiento: str | None = None) -> str:
        """Da de alta a quien escribe (un contacto nuevo). Pide cada consentimiento por
        separado y explícitamente; nunca los marques como true sin un sí claro.

        Args:
            nombre: Nombre y apellido.
            acepta_privacidad: Ha aceptado la política de privacidad.
            email: Email (para facturas).
            nivel: 'inicial', 'intermedio' o 'avanzado'.
            objetivos: Qué busca con el pilates.
            como_nos_conocio: Cómo conoció el estudio.
            consent_salud: Consiente que guardemos lesiones/limitaciones para sus instructores.
            consent_marketing: Consiente recibir promociones.
            fecha_nacimiento: 'AAAA-MM-DD' (opcional, para felicitarle).
        """
        return c("alta_cliente", clientes.alta_cliente, nombre=nombre,
                 acepta_privacidad=acepta_privacidad, email=email, nivel=nivel,
                 objetivos=objetivos, como_nos_conocio=como_nos_conocio,
                 consent_salud=consent_salud, consent_marketing=consent_marketing,
                 fecha_nacimiento=fecha(fecha_nacimiento) if fecha_nacimiento else None)

    @beta_tool
    def estado_bono() -> str:
        """Bonos y mensualidades de quien escribe: sesiones restantes y caducidad."""
        return c("estado_bono", bonos.estado_bono)

    @beta_tool
    def catalogo_bonos() -> str:
        """Catálogo de bonos, mensualidades y clases sueltas con precios."""
        return c("catalogo_bonos", bonos.catalogo)

    @beta_tool
    def mi_ficha() -> str:
        """Datos y consentimientos de quien escribe."""
        return c("mi_ficha", clientes.mi_ficha)

    @beta_tool
    def actualizar_consentimientos(consent_salud: bool | None = None,
                                   consent_marketing: bool | None = None) -> str:
        """Cambia los consentimientos de quien escribe.

        Args:
            consent_salud: Guardar lesiones/limitaciones para sus instructores.
            consent_marketing: Recibir promociones.
        """
        return c("actualizar_consentimientos", clientes.actualizar_consentimientos,
                 consent_salud=consent_salud, consent_marketing=consent_marketing)

    @beta_tool
    def registrar_lesion(descripcion: str) -> str:
        """Registra una lesión o limitación de quien escribe y avisa a sus instructores.
        Requiere consentimiento de salud. No des consejos médicos.

        Args:
            descripcion: Descripción breve en palabras del cliente.
        """
        return c("registrar_lesion", clientes.registrar_lesion, descripcion=descripcion)

    @beta_tool
    def lesion_resuelta() -> str:
        """Marca como resueltas las lesiones activas de quien escribe."""
        return c("lesion_resuelta", clientes.dar_lesion_por_resuelta)

    @beta_tool
    def congelar_bono(motivo: str, dias: int) -> str:
        """Pide congelar el bono activo. Crea una acción pendiente de confirmación.

        Args:
            motivo: Motivo (viaje, lesión…).
            dias: Días a congelar.
        """
        return c("congelar_bono", bonos.congelar_bono, motivo=motivo, dias=dias)

    @beta_tool
    def borrar_mis_datos() -> str:
        """Pide borrar todos los datos de quien escribe (derecho de supresión).
        Crea una acción pendiente de confirmación."""
        return c("borrar_mis_datos", clientes.borrar_mis_datos)

    @beta_tool
    def buscar_cliente(texto: str) -> str:
        """(Staff) Busca clientes por nombre, teléfono o email.

        Args:
            texto: Texto a buscar.
        """
        return c("buscar_cliente", clientes.buscar_cliente, texto=texto)

    @beta_tool
    def ficha_cliente(cliente_id: int) -> str:
        """(Staff) Ficha completa de un cliente: bonos, asistencias, lesiones y notas.

        Args:
            cliente_id: Id del cliente (de buscar_cliente).
        """
        return c("ficha_cliente", clientes.ficha_cliente, cliente_id=cliente_id)

    @beta_tool
    def nota_interna(cliente_id: int, texto: str) -> str:
        """(Staff) Añade una nota interna a un cliente (el cliente no la ve).

        Args:
            cliente_id: Id del cliente.
            texto: Nota.
        """
        return c("nota_interna", clientes.nota_interna, cliente_id=cliente_id, texto=texto)

    return [alta_cliente, estado_bono, catalogo_bonos, mi_ficha, actualizar_consentimientos,
            registrar_lesion, lesion_resuelta, congelar_bono, borrar_mis_datos,
            buscar_cliente, ficha_cliente, nota_interna]


def herramientas_cobros(ctx: Contexto) -> list[Any]:
    def c(herramienta: str, fn: Callable[..., Any], /, **kw: Any) -> str:
        return llamar(ctx, "cobros", herramienta, fn, **kw)

    @beta_tool
    def catalogo() -> str:
        """Catálogo de productos con su clave y precio."""
        return c("catalogo", bonos.catalogo)

    @beta_tool
    def crear_enlace_pago(producto: str) -> str:
        """Genera un enlace de pago para quien escribe. El producto se activa solo cuando
        el proveedor confirma el pago; nunca lo des por pagado tú.

        Args:
            producto: Clave del producto del catálogo (p. ej. 'bono10').
        """
        return c("crear_enlace_pago", pagos.crear_enlace_pago, producto=producto)

    @beta_tool
    def pagos_pendientes() -> str:
        """Pagos pendientes de quien escribe, con su enlace."""
        return c("pagos_pendientes", pagos.pagos_pendientes)

    @beta_tool
    def enviar_factura(pago_id: int | None = None) -> str:
        """Envía (o solicita al estudio) la factura de un pago de quien escribe.

        Args:
            pago_id: Id del pago; por defecto, el último.
        """
        return c("enviar_factura", pagos.enviar_factura, pago_id=pago_id)

    @beta_tool
    def registrar_pago_manual(cliente_id: int, producto: str, metodo: str,
                              importe_eur: float | None = None) -> str:
        """(Admin) Registra un pago en efectivo, Bizum o transferencia y activa el producto.
        Crea una acción pendiente de confirmación.

        Args:
            cliente_id: Id del cliente.
            producto: Clave del producto.
            metodo: 'efectivo', 'bizum', 'transferencia'…
            importe_eur: Importe si difiere del precio de catálogo.
        """
        return c("registrar_pago_manual", pagos.registrar_pago_manual, cliente_id=cliente_id,
                 producto=producto, metodo=metodo, importe_eur=importe_eur)

    @beta_tool
    def devolver_pago(pago_id: int, motivo: str) -> str:
        """(Admin) Pide devolver un pago. Crea una acción pendiente de confirmación.

        Args:
            pago_id: Id del pago.
            motivo: Motivo de la devolución.
        """
        return c("devolver_pago", pagos.devolver_pago, pago_id=pago_id, motivo=motivo)

    @beta_tool
    def resumen_caja(desde: str, hasta: str) -> str:
        """(Admin) Cobros, devoluciones y pendientes entre dos fechas (incluidas).

        Args:
            desde: 'AAAA-MM-DD'.
            hasta: 'AAAA-MM-DD'.
        """
        return c("resumen_caja", pagos.resumen_caja, desde=fecha(desde), hasta=fecha(hasta))

    @beta_tool
    def buscar_cliente(texto: str) -> str:
        """(Staff) Busca clientes por nombre, teléfono o email.

        Args:
            texto: Texto a buscar.
        """
        return c("buscar_cliente", clientes.buscar_cliente, texto=texto)

    return [catalogo, crear_enlace_pago, pagos_pendientes, enviar_factura,
            registrar_pago_manual, devolver_pago, resumen_caja, buscar_cliente]


def herramientas_marketing(ctx: Contexto) -> list[Any]:
    def c(herramienta: str, fn: Callable[..., Any], /, **kw: Any) -> str:
        return llamar(ctx, "marketing", herramienta, fn, **kw)

    @beta_tool
    def darse_de_baja_promos() -> str:
        """Quien escribe deja de recibir promociones (respuesta a 'BAJA')."""
        return c("darse_de_baja_promos", clientes.darse_de_baja_promos)

    @beta_tool
    def clientes_inactivos(dias: int = 21) -> str:
        """(Admin) Clientes activos que llevan más de N días sin venir.

        Args:
            dias: Días sin venir.
        """
        return c("clientes_inactivos", marketing.clientes_inactivos, dias=dias)

    @beta_tool
    def segmentar(nivel: str | None = None, modalidad: str | None = None,
                  con_bono_activo: bool | None = None, inactivos_dias: int | None = None) -> str:
        """(Admin) Cuenta los clientes con consentimiento de marketing que cumplen un filtro.

        Args:
            nivel: 'inicial', 'intermedio' o 'avanzado'.
            modalidad: Han hecho alguna vez 'reformer' o 'suelo'.
            con_bono_activo: true = con bono activo; false = sin bono activo.
            inactivos_dias: Solo quienes llevan más de N días sin venir.
        """
        return c("segmentar", marketing.segmentar, nivel=nivel, modalidad=modalidad,
                 con_bono_activo=con_bono_activo, inactivos_dias=inactivos_dias)

    @beta_tool
    def borrador_campana(texto: str, nivel: str | None = None, modalidad: str | None = None,
                         con_bono_activo: bool | None = None,
                         inactivos_dias: int | None = None) -> str:
        """(Admin) Guarda el borrador de una campaña con su segmento. No envía nada.

        Args:
            texto: Mensaje de la campaña (breve, tono WhatsApp).
            nivel: Filtro de nivel.
            modalidad: Filtro de modalidad.
            con_bono_activo: Filtro de bono activo.
            inactivos_dias: Filtro de inactividad.
        """
        return c("borrador_campana", marketing.borrador_campana, texto=texto, nivel=nivel,
                 modalidad=modalidad, con_bono_activo=con_bono_activo,
                 inactivos_dias=inactivos_dias)

    @beta_tool
    def enviar_campana(campana_id: int) -> str:
        """(Admin) Pide enviar una campaña. Crea una acción pendiente de confirmación.

        Args:
            campana_id: Id del borrador.
        """
        return c("enviar_campana", marketing.enviar_campana, campana_id=campana_id)

    @beta_tool
    def informe_semanal() -> str:
        """(Admin) Ocupación, altas, ingresos y clientes en riesgo de los últimos 7 días."""
        return c("informe_semanal", marketing.informe_semanal)

    return [darse_de_baja_promos, clientes_inactivos, segmentar, borrador_campana,
            enviar_campana, informe_semanal]


FABRICAS: dict[str, Callable[[Contexto], list[Any]]] = {
    "reservas": herramientas_reservas,
    "clientes": herramientas_clientes,
    "cobros": herramientas_cobros,
    "marketing": herramientas_marketing,
}


# Herramientas que solo se le enseñan al staff. Es defensa en profundidad: el servicio
# vuelve a comprobar el rol con @requiere_rol aunque el modelo intente llamarlas.
SOLO_STAFF = {"clases_del_instructor", "asistentes_clase", "marcar_asistencia",
              "buscar_cliente", "ficha_cliente", "nota_interna"}
SOLO_ADMIN = {"crear_clase", "modificar_clase", "cancelar_clase", "registrar_pago_manual",
              "devolver_pago", "resumen_caja", "clientes_inactivos", "segmentar",
              "borrador_campana", "enviar_campana", "informe_semanal"}
SOLO_LEAD = {"alta_cliente"}


def herramientas_para(subagente: str, ctx: Contexto) -> list[Any]:
    from app.servicios.base import Rol

    def visible(nombre: str) -> bool:
        if nombre in SOLO_ADMIN:
            return ctx.rol >= Rol.ADMIN
        if nombre in SOLO_STAFF:
            return ctx.rol >= Rol.INSTRUCTOR
        if nombre in SOLO_LEAD:
            return ctx.rol == Rol.LEAD
        return True

    return [t for t in FABRICAS[subagente](ctx) if visible(t.name)]
