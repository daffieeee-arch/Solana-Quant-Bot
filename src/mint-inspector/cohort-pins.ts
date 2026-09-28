// Exact mirror of native snapshot pins; parity is checked offline. No runtime admission override.
export const COHORT_PINS_SHA256 = "0965f63b62359d7891c7281b536916cd575947e0e986b0a98051912da6fe508b";
export const COHORT_PINS = {
  "schema": "OF1_B7_DEVELOPMENT_COHORT_PINS_1",
  "selection_sha256": "085d33c70ad504a9e14378629df7ec584c88826dc918f4eafb780bb44c824782",
  "evidence_report_sha256": "57768a8844e38be2f7cbf9833a53eb355cc29628dbc2fa0170a11a133c2f939d",
  "windows": [
    {
      "ordinal": "0",
      "relative_manifest": "work/w00/continuation-1/collection.json",
      "sha256": "371690ef49fd41a9e8221f2626546df32e93bd18d8d0793396be6d89e9e40511",
      "range": [
        "422526144",
        "422526160"
      ],
      "counts": {
        "blocks": "16",
        "packages": "21068",
        "failures": "2965",
        "silver_facts": "57"
      },
      "layers": {
        "bronze": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "b394c1764b50a685d2bccfeb5686f6b4fe063955d399db9795ef53ef533c6952",
          "rows": "21068"
        },
        "silver": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "c879cb5e96a27d783530b6a33d593fc29c00f5d58164aaa45e14572ea3eab13e",
          "rows": "57"
        }
      }
    },
    {
      "ordinal": "1",
      "relative_manifest": "work/w01/collection.json",
      "sha256": "e2c7646265824e578202c1688f98c8c64c78e9114bb74a723e66a65292b2c305",
      "range": [
        "422552336",
        "422552352"
      ],
      "counts": {
        "blocks": "16",
        "packages": "20962",
        "failures": "3079",
        "silver_facts": "19"
      },
      "layers": {
        "bronze": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "bb93b0909fa192e2c4e8e23917968203eaeb43cca3e14bb29db0e4862c337303",
          "rows": "20962"
        },
        "silver": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "88dbfa6588ba141ecbbfeca95f82dfcce1d5d7794027a11e7df1232432855153",
          "rows": "19"
        }
      }
    },
    {
      "ordinal": "2",
      "relative_manifest": "work/w02/collection.json",
      "sha256": "24db55a627310eb596fb128005cd96ad57527cabdbd0b8300caf258e7b74f8fb",
      "range": [
        "422692432",
        "422692448"
      ],
      "counts": {
        "blocks": "16",
        "packages": "20267",
        "failures": "2311",
        "silver_facts": "13"
      },
      "layers": {
        "bronze": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "2168bcef10c63331e31e04cfbcb93d437338e7741582152cc2aa1907fee7872e",
          "rows": "20267"
        },
        "silver": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "2c0726d5916a6788db2011ab04cc43b7979271ff5a57468134d21f012cf5677a",
          "rows": "13"
        }
      }
    },
    {
      "ordinal": "3",
      "relative_manifest": "work/w03/collection.json",
      "sha256": "b49c3d4a3cbeb146ef20d16a0b1300c4c2b4c982e44011eef97b950e91ab9cb5",
      "range": [
        "422659936",
        "422659952"
      ],
      "counts": {
        "blocks": "16",
        "packages": "18244",
        "failures": "1150",
        "silver_facts": "33"
      },
      "layers": {
        "bronze": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "71e1e968bdd5227f0123488d99304417b97911448938269114cda0da14ede412",
          "rows": "18244"
        },
        "silver": {
          "logical_hash_algorithm": "OF1_ORDERED_RECORD_CHAIN_1",
          "ordered_logical_sha256": "76ba178303987d9003ad7eae8847fb7a085626eb7c5ea6bb4fb8d5b19b2fd713",
          "rows": "33"
        }
      }
    }
  ]
} as const;
