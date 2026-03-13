"""CLI entry point for {{ site_name }} harness."""

import json
import sys

import click

{% for domain in domains %}
from .core import {{ domain.name }}
{% endfor %}
from .utils.http_client import HttpClient
from .utils.output import output_error, output_result, output_success


@click.group()
@click.option("--base-url", envvar="{{ env_prefix }}_BASE_URL", default=None, help="Backend base URL")
@click.option("--{{ auth_option }}", envvar="{{ env_prefix }}_{{ auth_env_suffix }}", default=None, help="Auth {{ auth_type }}")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as JSON")
{% for opt in global_options %}
@click.option("--{{ opt.flag }}", envvar="{{ opt.envvar }}", type={{ opt.type }}, default={{ opt.default }}, help="{{ opt.help }}")
{% endfor %}
@click.pass_context
def cli(ctx, base_url, {{ auth_param }}, as_json, {{ global_option_params }}):
    """CLI harness for {{ site_name }}."""
    ctx.ensure_object(dict)
    ctx.obj = {
        "client": HttpClient(base_url=base_url, {{ auth_param }}={{ auth_param }}),
        "json": as_json,
    }


def _client(ctx) -> HttpClient:
    return ctx.obj["client"]


def _json(ctx) -> bool:
    return ctx.obj["json"]


{% for domain in domains %}
# ── {{ domain.name | capitalize }} commands ──

@click.group()
def {{ domain.name }}_cmd():
    """{{ domain.description }}"""

{% for endpoint in domain.endpoints %}
@{{ domain.name }}_cmd.command("{{ endpoint.command_name }}")
{% for param in endpoint.params %}
{% if param.required %}
@click.argument("{{ param.name }}", type={{ param.click_type }})
{% else %}
@click.option("--{{ param.flag }}", type={{ param.click_type }}, default={{ param.default }}, help="{{ param.help }}")
{% endif %}
{% endfor %}
@click.pass_context
def {{ domain.name }}_{{ endpoint.func_name }}(ctx, {{ endpoint.param_names }}):
    """{{ endpoint.description }}"""
    data = {{ domain.name }}.{{ endpoint.core_func }}(_client(ctx), {{ endpoint.call_args }})
    output_result(data, _json(ctx){{ endpoint.table_columns }})

{% endfor %}
{% endfor %}

# ── REPL mode ──

@cli.command("repl")
@click.pass_context
def repl(ctx):
    """Start interactive REPL mode."""
    import shlex

    click.echo("{{ site_name }} CLI REPL. Type 'help' for commands, 'quit' to exit.")
    while True:
        try:
            line = input("{{ site_name }}> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nBye!")
            break
        if not line:
            continue
        if line in ("quit", "exit"):
            break
        if line == "help":
            click.echo(cli.get_help(ctx))
            continue
        try:
            args = shlex.split(line)
            cli.main(args, standalone_mode=False, **ctx.params)
        except SystemExit:
            pass
        except Exception as e:
            output_error(str(e))


# Register groups
{% for domain in domains %}
cli.add_command({{ domain.name }}_cmd, "{{ domain.name }}")
{% endfor %}


def main():
    cli()


if __name__ == "__main__":
    main()
