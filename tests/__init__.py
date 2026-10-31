"""Tests never touch the network: any socket connection made while they run fails."""
import socket


def _refuse(*_args, **_kwargs):
    raise AssertionError("tests must not use the network")


socket.socket.connect = _refuse
socket.socket.connect_ex = _refuse
socket.create_connection = _refuse
