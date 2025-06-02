import argparse
import io
from pathlib import Path
import zstandard as zstd


def truncate_csv(input_path: Path, output_path: Path, lines: int) -> None:
    """Copy header and first N rows from a plain CSV file."""
    with input_path.open('r', encoding='utf-8') as f_in, \
            output_path.open('w', encoding='utf-8') as f_out:
        for i, line in enumerate(f_in):
            f_out.write(line)
            if i >= lines:
                break


def truncate_csv_zst(input_path: Path, output_path: Path, lines: int) -> None:
    """Decompress .csv.zst, truncate to N rows, recompress."""
    dctx = zstd.ZstdDecompressor()
    with input_path.open('rb') as f_in, dctx.stream_reader(f_in) as reader:
        text_reader = io.TextIOWrapper(reader, encoding='utf-8')
        lines_buf = []
        for i, line in enumerate(text_reader):
            lines_buf.append(line)
            if i >= lines:
                break
    cctx = zstd.ZstdCompressor()
    with output_path.open('wb') as f_out, cctx.stream_writer(f_out) as writer:
        for line in lines_buf:
            writer.write(line.encode('utf-8'))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create small test files by keeping only the first N rows of a CSV or CSV.ZST"
    )
    parser.add_argument('--input', required=True, help='Input CSV or CSV.ZST file')
    parser.add_argument('--output', required=True, help='Destination path for the tiny file')
    parser.add_argument('--lines', type=int, required=True, help='Number of data lines to keep')
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if input_path.suffix == '.zst' or input_path.name.endswith('.csv.zst'):
        truncate_csv_zst(input_path, output_path, args.lines)
    else:
        truncate_csv(input_path, output_path, args.lines)


if __name__ == '__main__':
    main()
