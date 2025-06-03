import argparse
import io
import json
from pathlib import Path
import zstandard as zstd


def truncate_csv(input_path: Path, output_path: Path, lines: int) -> None:
    """Copy header and first N rows from a plain CSV file."""
    with input_path.open("r", encoding="utf-8") as f_in, \
        output_path.open("w", encoding="utf-8") as f_out:
        for i, line in enumerate(f_in):
            f_out.write(line)
            if i >= lines:
                break


def truncate_csv_zst(input_path: Path, output_path: Path, lines: int) -> None:
    """Decompress .csv.zst, truncate to N rows, recompress."""
    dctx = zstd.ZstdDecompressor()
    with input_path.open("rb") as f_in, dctx.stream_reader(f_in) as reader:
        text_reader = io.TextIOWrapper(reader, encoding="utf-8")
        lines_buf = []
        for i, line in enumerate(text_reader):
            lines_buf.append(line)
            if i >= lines:
                break
    cctx = zstd.ZstdCompressor()
    with output_path.open("wb") as f_out, cctx.stream_writer(f_out) as writer:
        for line in lines_buf:
            writer.write(line.encode("utf-8"))


def truncate_json(input_path: Path, output_path: Path, lines: int) -> None:
    """Truncate a JSON array to the first N elements."""
    with input_path.open("r", encoding="utf-8") as f_in:
        data = json.load(f_in)
    if isinstance(data, list):
        data = data[:lines]
    with output_path.open("w", encoding="utf-8") as f_out:
        json.dump(data, f_out)


def truncate_json_zst(input_path: Path, output_path: Path, lines: int) -> None:
    """Decompress .json.zst, truncate to N elements if array, recompress."""
    dctx = zstd.ZstdDecompressor()
    with input_path.open("rb") as f_in, dctx.stream_reader(f_in) as reader:
        text_reader = io.TextIOWrapper(reader, encoding="utf-8")
        data = json.load(text_reader)
    if isinstance(data, list):
        data = data[:lines]
    cctx = zstd.ZstdCompressor()
    with output_path.open("wb") as f_out, cctx.stream_writer(f_out) as writer:
        with io.TextIOWrapper(writer, encoding="utf-8") as text_writer:
            json.dump(data, text_writer)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Create small test files by keeping only the first N rows of a CSV/JSON"
            " file. Compressed .zst variants are supported."
        )
    )
    parser.add_argument('--input', required=True, help='Input CSV/JSON file')
    parser.add_argument('--output', required=True, help='Destination path for the tiny file')
    parser.add_argument('--lines', type=int, required=True, help='Number of data lines/elements to keep')
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    suffix = input_path.suffix
    name = input_path.name
    if suffix == '.zst':
        if name.endswith('.csv.zst'):
            truncate_csv_zst(input_path, output_path, args.lines)
        elif name.endswith('.json.zst'):
            truncate_json_zst(input_path, output_path, args.lines)
        else:
            raise ValueError(f'Unknown compressed file type: {input_path}')
    else:
        if suffix == '.csv':
            truncate_csv(input_path, output_path, args.lines)
        elif suffix == '.json':
            truncate_json(input_path, output_path, args.lines)
        else:
            raise ValueError(f'Unsupported file extension: {input_path}')


if __name__ == '__main__':
    main()
