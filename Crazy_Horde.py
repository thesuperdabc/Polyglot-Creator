import chess
import chess.pgn
import chess.polyglot
import chess.variant
import datetime
import os

MAX_BOOK_PLIES = 20
MAX_BOOK_WEIGHT = 10000

def format_zobrist_key_hex(zobrist_key):
    return "%0.16x" % zobrist_key

def get_zobrist_key_hex(board):
    return format_zobrist_key_hex(chess.polyglot.zobrist_hash(board))

class BookMove:
    def __init__(self):
        self.weight = 0
        self.move = None

class BookPosition:
    def __init__(self):
        self.moves = {}
        self.fen = ""

    def get_move(self, uci):
        if uci in self.moves:
            return self.moves[uci]
        self.moves[uci] = BookMove()
        return self.moves[uci]

class Book:
    def __init__(self):
        self.positions = {}
        self.numpositions = 0
        self.nummoves = 0

    def get_position(self, zobrist_key_hex):
        if zobrist_key_hex in self.positions:
            return self.positions[zobrist_key_hex]
        self.positions[zobrist_key_hex] = BookPosition()
        return self.positions[zobrist_key_hex]

    def normalize_weights(self):
        for zobrist_key_hex in self.positions:
            bp = self.positions[zobrist_key_hex]
            max_weight = 0
            total_weight = 0
            for uci in bp.moves:
                bm = bp.moves[uci]
                if bm.weight > max_weight:
                    max_weight = bm.weight
                total_weight += bm.weight
            if max_weight > MAX_BOOK_WEIGHT:
                for uci in bp.moves:
                    bm = bp.moves[uci]
                    bm.weight = int(bm.weight / total_weight * MAX_BOOK_WEIGHT)

    def save_as_polyglot(self, path):
        self.numpositions = 0
        self.nummoves = 0
        with open(path, 'wb') as outfile:
            allentries = []
            for zobrist_key_hex in self.positions:
                zbytes = bytes.fromhex(zobrist_key_hex)
                bp = self.positions[zobrist_key_hex]
                posnotcounted = True
                for uci in bp.moves:
                    m = bp.moves[uci].move
                    mi = m.to_square + (m.from_square << 6)
                    if m.promotion is not None:
                        mi += ((m.promotion - 1) << 12)
                    elif m.drop is not None:  # Handle Crazyhouse drops
                        mi += ((m.drop - 1) << 12)
                    mbytes = bytes.fromhex("%0.4x" % mi)
                    weight = bp.moves[uci].weight
                    wbytes = bytes.fromhex("%0.4x" % weight)
                    lbytes = bytes.fromhex("%0.8x" % 0)
                    allbytes = zbytes + mbytes + wbytes + lbytes
                    if weight > 0:
                        allentries.append(allbytes)
                        self.nummoves += 1
                        if posnotcounted:
                            self.numpositions += 1
                            posnotcounted = False
            sorted_weights = sorted(allentries, key=lambda entry: entry[10:12], reverse=True)
            sorted_entries = sorted(sorted_weights, key=lambda entry: entry[0:8])
            print("total of {} moves added to book {}".format(len(allentries), path))
            for entry in sorted_entries:
                outfile.write(entry)

    def merge_file(self, path):
        reader = chess.polyglot.open_reader(path)
        cnt = 0
        for entry in reader:
            cnt += 1
            zobrist_key_hex = format_zobrist_key_hex(entry.key)
            bp = self.get_position(zobrist_key_hex)
            move = entry.move()
            uci = move.uci()
            bm = bp.get_move(uci)
            bm.move = move
            bm.weight += entry.weight
            if cnt % 10000 == 0:
                print("merged {} moves".format(cnt))

class LichessGame:
    def __init__(self, game):
        self.game = game

    def get_id(self):
        url = self.game.headers["Site"]
        parts = url.split("/")
        game_id = parts[-1]
        return game_id

    def get_time(self):
        dtstr = self.game.headers["UTCDate"] + "T" + self.game.headers["UTCTime"]
        dtobj = datetime.datetime(1970, 1, 1)
        try:
            gamedt = dtobj.strptime(dtstr, "%Y.%m.%dT%H:%M:%S")
        except ValueError:
            gamedt = datetime.datetime.now()
        return gamedt.timestamp()

    def result(self):
        return self.game.headers.get("Result", "*")

    def white(self):
        return self.game.headers.get("White", "?")

    def black(self):
        return self.game.headers.get("Black", "?")

    def score(self):
        res = self.result()
        if res == "1/2-1/2":
            return 1
        if res == "1-0":
            return 2
        return 0

def build_book_file(pgnpath, bookpath):
    if not os.path.exists(pgnpath):
        print(f"Error: PGN file {pgnpath} not found")
        return
    print("building book {} from {}".format(bookpath, pgnpath))

    book = Book()
    pgn = open(pgnpath)

    cnt = 0

    while True:
        try:
            rawgame = chess.pgn.read_game(pgn)
            if rawgame is None:
                break
        except Exception as e:
            print(f"Error reading game {cnt + 1}: {e}")
            continue

        variant = rawgame.headers.get("Variant", "Standard")
        if variant == "Crazyhouse":
            board = chess.variant.CrazyhouseBoard()
        elif variant == "Horde":
            board = chess.variant.HordeBoard()
        else:
            print(f"Skipping unsupported variant: {variant}")
            continue

        # Set initial FEN if provided in PGN
        if "FEN" in rawgame.headers:
            board.set_fen(rawgame.headers["FEN"])

        ligame = LichessGame(rawgame)

        cnt += 1
        if cnt % 100 == 0:
            print("added {:8d} games".format(cnt))

        zobrist_key_hex = get_zobrist_key_hex(board)
        bp = book.get_position(zobrist_key_hex)
        bp.fen = board.fen()

        ply = 0
        for move in rawgame.mainline():
            if ply < MAX_BOOK_PLIES:
                uci = move.move.uci()

                # Handle castling moves for standard-like behavior
                from_square = move.move.from_square
                if from_square is not None:  # Normal move, not a drop
                    fromp = board.piece_at(from_square)
                    if fromp and fromp.piece_type == chess.KING:
                        if uci == "e1g1":
                            uci = "e1h1"
                        elif uci == "e1c1":
                            uci = "e1a1"
                        elif uci == "e8g8":
                            uci = "e8h8"
                        elif uci == "e8c8":
                            uci = "e8a8"

                bm = bp.get_move(uci)
                bm.move = move.move

                game_score = ligame.score()
                score_corr = game_score if board.turn == chess.WHITE else 2 - game_score
                bm.weight += score_corr

                board.push(move.move)
                zobrist_key_hex = get_zobrist_key_hex(board)
                bp = book.get_position(zobrist_key_hex)
                bp.fen = board.fen()

                ply += 1
            else:
                break

    book.normalize_weights()
    book.save_as_polyglot(bookpath)

    pgn.close()

if __name__ == "__main__":
    build_book_file("crazy.pgn", "crazy.bin")
