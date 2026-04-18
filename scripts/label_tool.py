# scripts/label_tool.py
import firebase_admin
from firebase_admin import firestore
import sys

LABELS = {
    '0': 'Normal',
    '1': 'Overheating',
    '2': 'Overcurrent',
    '3': 'Abnormal Vibration',
    '4': 'Low Oil',
    '5': 'Combined Fault',
    's': 'SKIP',
    'q': 'QUIT'
}

def main():
    db = firestore.client()
    queue = db.collection('review_queue').where('status', '==', 'pending') \
               .order_by('created_at').limit(50).get()

    print(f"Pending reviews: {len(queue)}\n")

    for item in queue:
        qdata = item.to_dict()
        rid   = qdata['reading_id']
        rdoc  = db.collection('readings').document(rid).get()
        if not rdoc.exists:
            continue
        r = rdoc.to_dict()

        print(f"\n── Reading {rid} ──")
        print(f"  Time:         {r.get('timestamp')}")
        print(f"  Winding Temp: {r.get('winding_temp'):.2f} °C")
        print(f"  Current:      {r.get('current'):.3f} A")
        print(f"  Vibration:    {r.get('vibration'):.4f}")
        print(f"  Oil Level:    {r.get('oil_level'):.1f} %")
        print(f"  Entropy:      {qdata.get('entropy'):.4f}  ← model uncertainty")
        print(f"  Labels: {LABELS}")

        choice = input("  Label > ").strip().lower()
        if choice == 'q':
            print("Quit.")
            sys.exit(0)
        if choice == 's':
            continue
        if choice not in LABELS:
            print("Invalid — skipping")
            continue

        db.collection('readings').document(rid).update({
            'label':      int(choice),
            'labeled_at': firestore.SERVER_TIMESTAMP
        })
        item.reference.update({'status': 'done'})
        print(f"  Saved: {LABELS[choice]}")

if __name__ == '__main__':
    main()